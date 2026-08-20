"""
canviz/bus.py
-------------
Manages the python-can Bus lifecycle.

Priority order (matches the design decision log):
  1. gs_usb  — Candlelight firmware, no COM port, plug-and-play on Windows
  2. slcan   — COM port devices (secondary path)
  3. virtual — software bus, used for dev and CI (no hardware needed)
  4. socketcan — Linux SocketCAN (Raspberry Pi, WSL2)

Disconnect/reconnect design
---------------------------
On Windows, gs_usb (libusb/WinUSB) does not reliably release the USB device
handle within a short time after shutdown(). Attempting to reopen within ~5s
consistently raises [Errno 13] Access denied.

Solution: "disconnect" is a SOFT operation — it stops the frame reader loop
but keeps the USB bus object alive. On reconnect with the same settings, the
existing handle is reused immediately (no USB re-enumeration needed).

A full hardware teardown (_hard_shutdown) is only done:
  - When the server process exits (lifespan shutdown)
  - When the user explicitly changes interface/bitrate/channel (settings change)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

import can
from can import Message

from canviz.config import settings, InterfaceType

log = logging.getLogger("canviz.bus")


class BusManager:
    def __init__(self) -> None:
        self._bus: Optional[can.BusABC] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._connected: bool = False
        self._error: Optional[str] = None
        self._frame_callbacks: list[Callable[[Message], None]] = []
        self._open_time: float = 0.0
        self._open_interface: Optional[str] = None
        self._open_channel: str = ""
        self._open_bitrate: int = 0
        self._open_index: int = 0
        self._open_serial_baudrate: int = 0  
        self._echoes_sent_frames: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def error(self) -> Optional[str]:
        return self._error

    def add_frame_callback(self, cb: Callable[[Message], None]) -> None:
        if cb not in self._frame_callbacks:
            self._frame_callbacks.append(cb)

    def remove_frame_callback(self, cb: Callable[[Message], None]) -> None:
        self._frame_callbacks = [c for c in self._frame_callbacks if c is not cb]

    async def connect(
        self,
        interface: InterfaceType,
        channel: str = "",
        bitrate: int = 500_000,
        index: int = 0,
        baudrate: int = 115_200
    ) -> None:
        if self._connected or self._bus is not None:
            await self.disconnect()

        self._error = None

        try:
            self._bus = _open_bus(interface, channel, bitrate, index, baudrate)
        except Exception as exc:
            self._error = str(exc)
            log.error("Bus open failed: %s", exc)
            raise

        self._open_interface = interface
        self._open_channel   = channel
        self._open_bitrate   = bitrate
        self._open_serial_baudrate = baudrate
        self._open_index     = index

        self._echoes_sent_frames = interface in ("gs_usb", "virtual", "dynosure-slcan")

        settings.interface = interface
        settings.channel   = channel
        settings.bitrate   = bitrate
        settings.index     = index

        self._open_time  = time.monotonic()
        self._connected  = True
        self._reader_task = asyncio.get_event_loop().create_task(
            self._reader_loop(), name="can-reader"
        )
        log.info(
            "Connected: interface=%s channel=%s bitrate=%d",
            interface, channel, bitrate,
        )

    async def disconnect(self) -> None:
        """
        Hard disconnect — cancels reader task, closes hardware bus handle,
        and releases device resources completely.
        """
        self._connected = False

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._bus is not None:
            bus = self._bus
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, _release_bus_resources, bus)
                log.info("Bus hardware released.")
            except Exception as exc:
                log.warning("Bus release error: %s", exc)

            self._bus = None
            self._open_interface = None
            self._open_channel = ""
            self._open_bitrate = 0
            self._open_index = 0
            self._open_serial_baudrate = 0

            await asyncio.sleep(0.5)

        log.info("Disconnected.")

    async def _hard_shutdown(self) -> None:
        """
        Full hardware teardown.
        """
        await self.disconnect()

    async def send(self, arbitration_id: int, data: list[int], is_extended_id: bool = False) -> None:
        if not self._connected or self._bus is None:
            raise RuntimeError("Not connected — call /connect first")
        msg = can.Message(
            arbitration_id=arbitration_id,
            data=bytes(data),
            is_extended_id=is_extended_id,
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._bus.send, msg)

        if not self._echoes_sent_frames:
            msg.timestamp = time.monotonic() - self._open_time
            for cb in list(self._frame_callbacks):
                try:
                    cb(msg)
                except Exception as exc:
                    log.warning("Frame callback error on tx echo: %s", exc)

    async def _reader_loop(self) -> None:
        log.debug("Reader loop started.")
        loop = asyncio.get_event_loop()
        _consecutive_none = 0

        while self._connected and self._bus is not None:
            try:
                msg: Optional[Message] = await loop.run_in_executor(
                    None, self._bus.recv, 0.1
                )
            except Exception as exc:
                log.warning("recv error: %s", exc)
                await asyncio.sleep(0.1)
                continue

            if msg is None:
                _consecutive_none += 1
                if (
                    _consecutive_none == 300
                    and self._open_interface == "slcan"
                ):
                    log.warning(
                        "slcan: no frames received in ~30 s. "
                        "Check: (1) CAN bitrate matches the bus (%d bps), "
                        "(2) serial baud rate matches adapter (current: %d). "
                        "Common fix: try Serial Baud Rate = 2000000 in the UI.",
                        self._open_bitrate,
                        self._open_serial_baudrate,
                    )
                continue

            _consecutive_none = 0
            msg.timestamp = time.monotonic() - self._open_time

            for cb in list(self._frame_callbacks):
                try:
                    cb(msg)
                except Exception as exc:
                    log.warning("Frame callback error: %s", exc)

        log.debug("Reader loop exited.")


# ── Internal helpers ─────────────────────────────────────────────────────────

def _find_libusb_backend():
    """
    On Windows, pyusb cannot find libusb automatically.
    Try to locate it from the pip-installed 'libusb' package first,
    then fall back to letting pyusb search system paths.
    Returns a usb backend object or None.
    """
    import usb.backend.libusb1 as libusb1_backend
    try:
        import libusb
        dll_path = libusb.dll._name
        log.debug("Using bundled libusb DLL: %s", dll_path)
        backend = libusb1_backend.get_backend(find_library=lambda x: dll_path)
        if backend:
            return backend
    except Exception as exc:
        log.debug("Bundled libusb not usable (%s), falling back to system search", exc)

    backend = libusb1_backend.get_backend()
    return backend


_libusb_patched: bool = False  # guard against stacking the monkey-patch on reconnect


def _ensure_libusb() -> None:
    """
    Verify pyusb can reach a libusb backend.
    On Windows the 'libusb' pip package bundles the DLL but pyusb won't
    find it automatically — we patch usb.core to use it explicitly.
    Raises ImportError with a clear action if nothing works.
    """
    global _libusb_patched
    if _libusb_patched:
        return

    try:
        import usb.core
        import usb.backend.libusb1 as libusb1_backend  # noqa: F401

        backend = _find_libusb_backend()
        if backend is None:
            raise RuntimeError("no backend found")

        _original_find = usb.core.find

        def _find_with_backend(*args, **kwargs):
            kwargs.setdefault("backend", backend)
            return _original_find(*args, **kwargs)

        usb.core.find = _find_with_backend
        _libusb_patched = True
        log.debug("pyusb backend patched successfully")

    except Exception as exc:
        raise ImportError(
            f"pyusb could not find a libusb backend: {exc}\n\n"
            "Fix (Windows):\n"
            "  2. If that still fails, download libusb-1.0.dll from https://libusb.info\n"
            "     and place it next to python.exe\n"
            "     (e.g. C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python312\\)\n"
        ) from exc


def _release_bus_resources(bus: can.BusABC) -> None:
    # 1. Handle gs_usb specially to avoid python-can't GsUsb.scan() re-open bug on Windows
    if hasattr(bus, "gs_usb"):
        try:
            # Set _index to None to bypass buggy scan block in python-can't shutdown()
            if hasattr(bus, "_index"):
                bus._index = None
        except Exception:
            pass

    # 2. Shutdown standard bus (this now runs fully/properly since _is_shutdown is False)
    try:
        bus.shutdown()
    except Exception as exc:
        log.debug("bus.shutdown() warning: %s", exc)

    # 3. Explicitly release pyusb / libusb C handle for the device
    if hasattr(bus, "gs_usb"):
        try:
            if hasattr(bus.gs_usb, "gs_usb"):
                import usb.util
                usb.util.dispose_resources(bus.gs_usb.gs_usb)
                log.debug("Disposed pyusb resources for gs_usb")
        except Exception as exc:
            log.debug("dispose_resources error: %s", exc)

    # 3. Clean up any remaining scanned pyusb devices for gs_usb
    try:
        import usb.core
        import usb.util
        import can.interfaces.gs_usb
        devs = usb.core.find(
            find_all=True,
            custom_match=can.interfaces.gs_usb.GsUsb.is_gs_usb_device,
        )
        if devs:
            for dev in devs:
                try:
                    usb.util.dispose_resources(dev)
                except Exception:
                    pass
    except Exception:
        pass


import queue
from typing import Tuple

class DynoSureSlcanBus(can.BusABC):
    def __init__(self, channel: str = "", bitrate: int = 500000, index: int = 0, **kwargs):
        super().__init__(channel=channel, **kwargs)
        
        import slcanv1
        self._slcan = slcanv1.SlcanV2()
        self._queue = queue.Queue()
        self._port = None
        
        devs = self._slcan.enum_devices()
        if not devs:
            raise ValueError("No DynoSure devices found on USB")
            
        selected_dev = None
        scanned_details = []
        for dev in devs:
            disp_name = getattr(dev, "displayName", b"").decode("utf-8", errors="ignore")
            serial_no = getattr(dev, "serialNo", b"").decode("utf-8", errors="ignore")
            path_str = getattr(dev, "devicePath", b"").decode("utf-8", errors="ignore")
            
            desc_text = f"{disp_name} {serial_no} {path_str}".lower()
            scanned_details.append(f"Name: {disp_name}, Serial: {serial_no}")
            
            if "dynosure" in desc_text or "can fd interface" in desc_text:
                if channel:
                    if channel in disp_name or channel in serial_no or channel in path_str:
                        selected_dev = dev
                        break
                else:
                    selected_dev = dev
                    break
        
        if not selected_dev:
            if not channel and index < len(devs):
                selected_dev = devs[index]
            else:
                scanned_summary = "; ".join(scanned_details)
                raise ValueError(
                    f"Interlock failed: No compatible device ('dynosure' or 'can fd interface') found. "
                    f"Scanned devices: [{scanned_summary}]"
                )
                
        self._port = selected_dev.devicePath
        
        rc = self._slcan.open_port(self._port)
        if rc != 1:
            raise ValueError(f"Failed to open DynoSure port {self._port.decode('utf-8', errors='ignore')}")
            
        # Set bitrate using 160 MHz clock calculations
        brp = int(8_000_000 / bitrate)
        rc = self._slcan.set_bitrate_advanced(is_fd=0, brp=brp, seg1=15, seg2=4, port_name=self._port)
        if rc != 0:
            self._slcan.close(self._port)
            raise ValueError(f"Failed to set bitrate {bitrate} (brp={brp})")
            
        def _rx_wrapper(packet_ptr):
            try:
                pkt = packet_ptr.contents
                data_len = pkt.dlc
                msg = can.Message(
                    arbitration_id=pkt.id,
                    is_extended_id=bool(pkt.ext),
                    is_fd=bool(pkt.fd),
                    is_remote_frame=bool(pkt.rtr),
                    dlc=data_len,
                    data=bytes(pkt.data[:data_len]),
                    timestamp=pkt.timestamp / 1000000.0,
                    channel=self.channel_info
                )
                self._queue.put(msg)
            except Exception:
                pass
                
        self._slcan.set_rx_callback(_rx_wrapper, port_name=self._port)
        
        # Start in loopback mode (SLCANV2_FLAG_LOOPBACK = 2)
        rc = self._slcan.start_with_flags(2, self._port)
        if rc != 0:
            self._slcan.close(self._port)
            raise ValueError("Failed to start DynoSure adapter in loopback mode")
            
        self.channel_info = selected_dev.displayName.decode("utf-8", errors="ignore")
        
    def _recv_internal(self, timeout: Optional[float]) -> Tuple[Optional[can.Message], bool]:
        try:
            msg = self._queue.get(timeout=timeout)
            return msg, False
        except queue.Empty:
            return None, False
            
    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        if not self._port:
            raise RuntimeError("Port not opened")
            
        import slcanv1
        pkt = slcanv1.SlcanV2.PacketFD()
        pkt.id = msg.arbitration_id
        pkt.dlc = msg.dlc
        pkt.ext = 1 if msg.is_extended_id else 0
        pkt.fd = 1 if msg.is_fd else 0
        pkt.rtr = 1 if msg.is_remote_frame else 0
        
        for i, val in enumerate(msg.data):
            pkt.data[i] = val
            
        timeout_ms = int(timeout * 1000) if timeout else 100
        rc = self._slcan.send_packet(pkt, timeout_ms, self._port)
        if rc != 0:
            raise RuntimeError(f"Failed to transmit packet: error code {rc}")
            
    def shutdown(self) -> None:
        super().shutdown()
        if self._port:
            port = self._port
            self._port = None
            try:
                self._slcan.close(port)
            except Exception:
                pass


def _open_bus(
    interface: InterfaceType,
    channel: str,
    bitrate: int,
    index: int,
    serial_baudrate: int = 115200,
) -> can.BusABC:
    if interface == "dynosure-slcan":
        _ensure_libusb()
        return DynoSureSlcanBus(channel=channel, bitrate=bitrate, index=index)

    elif interface == "gs_usb":
        _ensure_libusb()
        return can.Bus(interface="gs_usb", channel=index, bitrate=bitrate)

    elif interface == "slcan":
        if not channel:
            raise ValueError("slcan requires a channel (e.g. COM3 or /dev/ttyACM0)")
        log.info(
            "Opening slcan: channel=%s  CAN bitrate=%d bps  serial baud=%d",
            channel, bitrate, serial_baudrate,
        )
        # (open) command is sent *inside* can.Bus().__init__(). The settle
        # time must come AFTER the bus is constructed so the adapter has time
        # to process that command before we start calling recv().
        bus = can.Bus(
            interface="slcan",
            channel=channel,
            bitrate=bitrate,
            ttyBaudrate=serial_baudrate,
        )
        time.sleep(0.25)  # let adapter process O\r before first recv()
        return bus

    elif interface == "socketcan":
        if not channel:
            raise ValueError("socketcan requires a channel (e.g. can0)")
        return can.Bus(interface="socketcan", channel=channel, bitrate=bitrate)

    elif interface == "virtual":
        return can.Bus(interface="virtual", channel="vcan0", receive_own_messages=True)

    elif interface == "pcan":
        ch = channel if channel else "PCAN_USBBUS1"
        return can.Bus(interface="pcan", channel=ch, bitrate=bitrate)

    elif interface == "kvaser":
        return can.Bus(interface="kvaser", channel=index, bitrate=bitrate)

    elif interface == "vector":
        ch = channel if channel else 0
        try:
            ch = int(ch)
        except ValueError:
            pass
        return can.Bus(interface="vector", channel=ch, bitrate=bitrate)

    elif interface == "seeedstudio":
        if not channel:
            raise ValueError("seeedstudio requires a channel (e.g. COM8 or /dev/ttyUSB0)")
        log.info("Opening seeedstudio USB-CAN: channel=%s  CAN bitrate=%d bps", channel, bitrate)
        # Seeed Studio / GY USB-CAN Analyzer — binary 0xAA/0x55 framing protocol.
        # No serial baud rate param — the protocol configures the device via an
        # init frame, not by matching a serial baud rate. python-can handles this
        # internally in the seeedstudio interface.
        return can.Bus(interface="seeedstudio", channel=channel, bitrate=bitrate)

    else:
        raise ValueError(
            f"Unknown interface: {interface!r}. "
            "Choose: gs_usb, slcan, socketcan, virtual, pcan, kvaser, seeedstudio, vector"
        )


def open_bus(
    interface: "InterfaceType",
    channel: str = "",
    bitrate: int = 500_000,
    index: int = 0,
    serial_baudrate: int = 115200,  
) -> "can.BusABC":
    """
    Public wrapper around _open_bus().
    Used by CLI subcommands (monitor, capture) that bypass FastAPI entirely.
    """
    return _open_bus(interface, channel, bitrate, index, serial_baudrate)


# Singleton
bus_manager = BusManager()