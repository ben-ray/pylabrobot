import asyncio
import logging
import random
import socket
import xml.etree.ElementTree as ET
from typing import Any, Dict

import aiohttp
from aiohttp import web


logger = logging.getLogger("pylabrobot.thermocycling.odtc")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)


class AsyncODTCClient:
    def __init__(self, ip: str = "10.0.0.51", port: int = 8080):
        self.ip = ip
        self.port = port
        self.url = f"http://{ip}:{port}/"
        self.session = None
        self.device_info = None
        self.request_id = random.randint(1000000000, 2000000000)
        self.lock_id = ""
        self._event_queue: asyncio.Queue[str] = asyncio.Queue()
        self._ns = {"s": "http://sila.coop"}
        self._server_runner = None

        # Determine local IP that can reach the device
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((self.ip, self.port))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = socket.gethostbyname(socket.gethostname())

        self.local_ip = local_ip
        self.event_uri = f"http://{local_ip}:7071/ihc"

    async def _sila_event_handler(self, request):
        xml_text = await request.text()
        logger.debug(f"Received SiLA event ({len(xml_text)} bytes)\n{xml_text}")

        self._event_queue.put_nowait(xml_text)

        # Determine response tag based on input
        if "ResponseEvent" in xml_text:
            resp_tag = "ResponseEventResponse"
        elif "StatusEvent" in xml_text:
            resp_tag = "StatusEventResponse"
        elif "ErrorEvent" in xml_text:
            resp_tag = "ErrorEventResponse"
        else:
            resp_tag = "ResponseEventResponse"

        soap_response = f'''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <s:{resp_tag} xmlns:s="http://sila.coop"/>
  </s:Body>
</s:Envelope>'''

        return web.Response(text=soap_response, content_type="text/xml")

    async def start_event_receiver(self):
        if self._server_runner:
            return

        app = web.Application()
        app.router.add_post("/ihc", self._sila_event_handler)
        self._server_runner = web.AppRunner(app)
        await self._server_runner.setup()
        site = web.TCPSite(self._server_runner, self.local_ip, 7071)
        await site.start()
        logger.info(f"SiLA Event Receiver listening on {self.event_uri}")

    async def _send_command(self, command: str, payload: str) -> str:
        envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:s="http://sila.coop">
  <soap:Body>
    <s:{command}>
      {payload}
    </s:{command}>
  </soap:Body>
</soap:Envelope>'''

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f"http://sila.coop/{command}",
            "Connection": "Close"
        }

        logger.debug(f"Sending command {command}:\n{envelope}")

        async with self.session.post(
            self.url, 
            data=envelope, 
            headers=headers, 
            timeout=10
        ) as response:
            response.raise_for_status()
            return await response.text()

    def _lock_id_xml(self) -> str:
        return f"<s:lockId>{self.lock_id}</s:lockId>" if self.lock_id else '<lockId i:nil="true"/>'

    async def connect(self) -> bool:
        await self.start_event_receiver()
        self.session = aiohttp.ClientSession()
        try:
            payload = f"<s:requestId>{self.request_id}</s:requestId><s:lockId>0</s:lockId>"
            xml = await self._send_command("GetDeviceIdentification", payload)
            self.request_id += 1
            
            root = ET.fromstring(xml)
            ns = self._ns
            
            device_desc = root.find(".//s:deviceDescription", ns)
            self.device_info = {
                "name": device_desc.find("s:DeviceName", ns).text,
                "serial": device_desc.find("s:DeviceSerialNumber", ns).text,
                "firmware": device_desc.find("s:DeviceFirmwareVersion", ns).text,
                "sila_version": device_desc.find("s:SiLAInterfaceVersion", ns).text,
                "wsdl": device_desc.find("s:Wsdl", ns).text
            }
            
            logger.info(f"Connected to {self.device_info['name']} (Firmware: {self.device_info['firmware']})")
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            if self.session:
                await self.session.close()
            return False

    async def initialize(self):
        await self._call_async("Initialize", "", accepted_timeout=40.0)

    async def reset_device(self) -> None:
        try:
            status = await self.get_status()
            device_id = status.get("device_id", "00000000-0000-0000-0000-000000000000")
        except Exception:
            device_id = "00000000-0000-0000-0000-000000000000"

        self.request_id += 1
        req_id = self.request_id

        envelope = f'''<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <Reset xmlns="http://sila.coop" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
      <requestId>{req_id}</requestId>
      {self._lock_id_xml()}
      <deviceId>{device_id}</deviceId>
      <eventReceiverURI>{self.event_uri}</eventReceiverURI>
      <PMSId>{self.event_uri}</PMSId>
      <errorHandlingTimeout i:nil="true"/>
      <simulationMode>false</simulationMode>
    </Reset>
  </s:Body>
</s:Envelope>'''

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://sila.coop/Reset"',
            "Connection": "Close"
        }

        logger.info(f"Sending Reset (requestId={req_id})")

        async with self.session.post(self.url, data=envelope, headers=headers, timeout=10) as resp:
            resp_text = await resp.text()

        root = ET.fromstring(resp_text)
        code_el = root.find(".//s:returnCode", self._ns)
        code = int(code_el.text) if code_el is not None else -1

        if code != 2:
            desc_el = root.find(".//s:description", self._ns) or root.find(".//s:message", self._ns)
            desc = desc_el.text if desc_el is not None else "unknown"
            raise RuntimeError(f"Reset rejected with code {code}: {desc}")

        try:
            await self._wait_for_request_completion(req_id, timeout=30.0)
        except TimeoutError:
            logger.warning("No completion event for Reset")

        self.lock_id = ""
        logger.info("Reset successful → device is unlocked and in Standby")
    
    async def lock_device(self, proposed_lock_id: int = 0) -> str:
        self.request_id += 1
        req_id = self.request_id

        payload = f"""<s:requestId>{req_id}</s:requestId>
    <s:lockId>{proposed_lock_id}</s:lockId>
    <s:lockTimeout i:nil="true" xmlns:i="http://www.w3.org/2001/XMLSchema-instance"/>
    <s:eventReceiverURI>{self.event_uri}</s:eventReceiverURI>
    <s:PMSId>{self.event_uri}</s:PMSId>"""

        envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:s="http://sila.coop">
  <soap:Body>
    <s:LockDevice>
      {payload}
    </s:LockDevice>
  </soap:Body>
</soap:Envelope>'''

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://sila.coop/LockDevice"',
            "Connection": "Close"
        }

        logger.info(f"LockDevice → requestId={req_id}, eventReceiverURI={self.event_uri}")

        async with self.session.post(self.url, data=envelope, headers=headers, timeout=10) as resp:
            response_xml = await resp.text()

        root = ET.fromstring(response_xml)
        ns = self._ns

        rc_elem = root.find(".//s:returnCode", ns)
        if rc_elem is None or rc_elem.text is None:
            raise RuntimeError(f"LockDevice immediate response missing returnCode:\n{response_xml[:500]}")

        code = int(rc_elem.text)
        desc_elem = root.find(".//s:description", ns) or root.find(".//s:message", ns)
        desc = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""

        if code != 2:
            raise RuntimeError(f"LockDevice not accepted (immediate code {code}): {desc}")

        logger.info("LockDevice accepted (code 2) – waiting for completion event")

        final_event_xml = await self._wait_for_request_completion(req_id, timeout=30.0)
        final_root = ET.fromstring(final_event_xml)

        ret_val = final_root.find(".//s:ReturnValue", ns)
        if ret_val is None:
            ret_val = final_root.find(".//s:LockDeviceResult", ns)
        if ret_val is None:
            ret_val = final_root.find(".//s:returnValue", ns)

        if ret_val is None:
            raise RuntimeError(f"LockDevice completion event has no <ReturnValue>. Response:\n{final_event_xml}")

        final_rc_elem = ret_val.find("s:returnCode", ns)
        if final_rc_elem is None or final_rc_elem.text is None:
            raise RuntimeError("LockDevice completion event missing returnCode")

        final_code = int(final_rc_elem.text)
        final_desc_elem = ret_val.find("s:description", ns) or ret_val.find("s:message", ns)
        final_desc = final_desc_elem.text.strip() if final_desc_elem is not None and final_desc_elem.text else ""

        if final_code != 3:
            raise RuntimeError(f"LockDevice failed in completion event (code {final_code}): {final_desc}")

        self.lock_id = str(proposed_lock_id)
        logger.info(f"Device successfully locked with lockId={self.lock_id}")
        return self.lock_id

    async def unlock_device(self) -> None:
        if not self.lock_id:
            logger.info("unlock_device: already unlocked or never locked")
            return

        self.request_id += 1
        req_id = self.request_id

        payload = f"""<s:requestId>{req_id}</s:requestId>
    <s:lockId>{self.lock_id}</s:lockId>
    <s:eventReceiverURI>{self.event_uri}</s:eventReceiverURI>
    <s:PMSId>{self.event_uri}</s:PMSId>"""

        envelope = f'''<?xml version="1.0" encoding="utf-8"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:s="http://sila.coop">
    <soap:Body>
        <s:UnlockDevice>
        {payload}
        </s:UnlockDevice>
    </soap:Body>
    </soap:Envelope>'''

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://sila.coop/UnlockDevice"',
            "Connection": "Close"
        }

        logger.info(f"UnlockDevice → releasing lockId={self.lock_id}")

        async with self.session.post(self.url, data=envelope, headers=headers, timeout=10) as resp:
            response_xml = await resp.text()

        root = ET.fromstring(response_xml)
        ns = self._ns

        rc_elem = root.find(".//s:returnCode", ns)
        if rc_elem is None:
            raise RuntimeError(f"UnlockDevice failed – no returnCode:\n{response_xml[:500]}")

        code = int(rc_elem.text)
        msg = (root.find(".//s:message", ns) or root.find(".//s:description", ns))
        msg_text = msg.text if msg is not None else ""

        if code != 1 and code != 2:
            raise RuntimeError(f"UnlockDevice failed: code {code} – {msg_text}")

        if code == 2:
            await self._wait_for_request_completion(req_id, timeout=30.0)

        logger.info("Device unlocked successfully")
        self.lock_id = ""

    async def get_status(self) -> Dict[str, Any]:
        xml = await self._send_command("GetStatus", f"<s:requestId>{self.request_id}</s:requestId>")
        self.request_id += 1
        root = ET.fromstring(xml)
        ns = {"s": "http://sila.coop"}
        
        return {
            "device_id": root.find(".//s:deviceId", ns).text,
            "state": root.find(".//s:state", ns).text,
            "locked": root.find(".//s:locked", ns).text == "true",
            "pms_id": root.find(".//s:PMSId", ns).text,
            "current_time": root.find(".//s:currentTime", ns).text
        }

    async def open_door(self):
        await self._call_async("OpenDoor", "", accepted_timeout=30.0)

    async def close_door(self):
        await self._call_async("CloseDoor", "", accepted_timeout=30.0)

    async def close(self):
        if self.session:
            await self.session.close()
        if self._server_runner:
            await self._server_runner.cleanup()

    async def _call_async(self, command: str, extra_payload: str, accepted_timeout: float = 10.0) -> ET.Element:
        if not self.lock_id and command not in ("LockDevice", "GetDeviceIdentification", "Reset"):
            logger.warning(f"Sending {command} without Lock ID. Events may not be received. Please call lock_device() first.")

        self.request_id += 1
        payload = (
            f"<s:requestId>{self.request_id}</s:requestId>"
            f"{self._lock_id_xml()}"
            f"{extra_payload}"
        )

        response_xml = await self._send_command(command, payload)
        root = ET.fromstring(response_xml)
        ns = self._ns
        return_el = root.find(".//s:ReturnValue", ns)

        if return_el is None:
            return_el = root.find(f".//s:{command}Result", ns)

        if return_el is None:
            fault = root.find(".//soap:Fault", {"soap": "http://schemas.xmlsoap.org/soap/envelope/"})
            if fault is not None:
                fault_string = fault.find("faultstring").text
                raise RuntimeError(f"SiLA SOAP Fault in {command}: {fault_string}")
            
            raise RuntimeError(f"No <ReturnValue> in {command} response. Response:\n{response_xml}")

        code_el = return_el.find("s:returnCode", ns)
        code = int(code_el.text) if code_el is not None else -1
        if code != 2:
            desc_el = return_el.find("s:description", ns)
            desc = desc_el.text if desc_el is not None else "unknown"
            raise RuntimeError(f"{command} not accepted (code {code}): {desc}")

        final_event_xml = await self._wait_for_request_completion(self.request_id, timeout=accepted_timeout)
        root = ET.fromstring(final_event_xml)

        rc_val = None
        msg_val = ""
        for elem in root.iter():
            if elem.tag.endswith("returnCode") and elem.text:
                try:
                    rc_val = int(elem.text.strip())
                except Exception:
                    pass
            if (elem.tag.endswith("message") or elem.tag.endswith("description")) and elem.text:
                msg_val = elem.text.strip()
        
        if rc_val is not None and rc_val not in (1, 3):
             raise RuntimeError(f"Async command {command} failed with event code {rc_val}: {msg_val}")

        return root

    def feed_event(self, event_xml: str):
        self._event_queue.put_nowait(event_xml)

    async def _wait_for_request_completion(self, request_id: int, timeout: float = 30.0) -> str:
        logger.debug(f"Waiting for event with requestId {request_id}")
        try:
            async with asyncio.timeout(timeout):
                while True:
                    event_xml = await self._event_queue.get()
                    try:
                        root = ET.fromstring(event_xml)
                    except ET.ParseError:
                        logger.warning("Failed to parse event XML")
                        continue
                    
                    rid_val = None
                    for elem in root.iter():
                        if elem.tag.endswith("requestId") and elem.text:
                            try:
                                rid_val = int(elem.text.strip())
                                break
                            except (TypeError, ValueError):
                                pass
                    
                    if rid_val == request_id:
                        return event_xml
                    else:
                        if rid_val is not None:
                            logger.debug(f"Ignored event with requestId {rid_val} (wanted {request_id})")
        except TimeoutError:
            raise TimeoutError(f"Timed out waiting for completion of request {request_id}")
