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
        self._buses: dict[str, can.BusABC] = {}
        self._reader_tasks: dict[str, asyncio.Task] = {}
        self._configs: dict[str, dict] = {}
        self._frame_callbacks: list[Callable[[Message], None]] = []
        self._open_time: float = 0.0
        self._error: Optional[str] = None

    @property
    def connected(self) -> bool:
        return len(self._buses) > 0

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
        self._error = None

        # Build a unique connection key for this device configuration
        ch_str = channel if channel else str(index)
        conn_key = f"{interface}:{ch_str}"

        # If already connected to this configuration, reuse it
        if conn_key in self._buses:
            log.info("Already connected to %s. Reusing connection.", conn_key)
            return

        try:
            bus = _open_bus(interface, channel, bitrate, index, baudrate)
        except Exception as exc:
            self._error = str(exc)
            log.error("Bus open failed for %s: %s", conn_key, exc)
            raise

        self._buses[conn_key] = bus
        self._configs[conn_key] = {
            "interface": interface,
            "channel": channel,
            "bitrate": bitrate,
            "index": index,
            "baudrate": baudrate,
            "echoes_sent_frames": interface in ("gs_usb", "virtual")
        }

        # Sync legacy settings helper
        settings.interface = interface
        settings.channel   = channel
        settings.bitrate   = bitrate
        settings.index     = index

        if self._open_time == 0.0:
            self._open_time = time.monotonic()

        task = asyncio.get_event_loop().create_task(
            self._reader_loop(conn_key, bus), name=f"can-reader-{conn_key}"
        )
        self._reader_tasks[conn_key] = task

        log.info(
            "Connected device %s: interface=%s channel=%s bitrate=%d",
            conn_key, interface, channel, bitrate,
        )

    async def disconnect(self, conn_key: Optional[str] = None) -> None:
        """
        Disconnect a specific bus connection by its key, or all of them if key is None.
        """
        if conn_key:
            # Cancel reader task
            if conn_key in self._reader_tasks:
                task = self._reader_tasks.pop(conn_key)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Shutdown the hardware
            if conn_key in self._buses:
                bus = self._buses.pop(conn_key)
                self._configs.pop(conn_key, None)
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, bus.shutdown)
                    log.info("Hardware connection %s released.", conn_key)
                except Exception as exc:
                    log.warning("Bus shutdown error for %s: %s", conn_key, exc)
        else:
            # Disconnect all active connections
            keys = list(self._buses.keys())
            for key in keys:
                await self.disconnect(key)

            # Reset open time if all disconnected
            self._open_time = 0.0

    async def _hard_shutdown(self) -> None:
        """Full teardown of all buses."""
        await self.disconnect()
        # Sleep briefly to let OS release handles
        await asyncio.sleep(1.0)

    async def send(self, arbitration_id: int, data: list[int], is_extended_id: bool = False, conn_key: Optional[str] = None) -> None:
        if not self._buses:
            raise RuntimeError("Not connected — call connect first")

        target_keys = [conn_key] if conn_key and conn_key in self._buses else list(self._buses.keys())

        msg = can.Message(
            arbitration_id=arbitration_id,
            data=bytes(data),
            is_extended_id=is_extended_id,
        )
        loop = asyncio.get_event_loop()

        for key in target_keys:
            bus = self._buses[key]
            try:
                await loop.run_in_executor(None, bus.send, msg)
                # Manually echo to UI if interface does not echo natively
                cfg = self._configs.get(key, {})
                if not cfg.get("echoes_sent_frames", False):
                    msg_echo = can.Message(
                        arbitration_id=arbitration_id,
                        data=bytes(data),
                        is_extended_id=is_extended_id,
                        timestamp=time.monotonic() - self._open_time,
                        channel=key
                    )
                    for cb in list(self._frame_callbacks):
                        try:
                            cb(msg_echo)
                        except Exception as exc:
                            log.warning("Frame callback error on tx echo: %s", exc)
            except Exception as exc:
                log.warning("Send failed on connection %s: %s", key, exc)

    async def _reader_loop(self, conn_key: str, bus: can.BusABC) -> None:
        log.debug("Reader loop started for connection %s.", conn_key)
        loop = asyncio.get_event_loop()
        _consecutive_none = 0

        cfg = self._configs.get(conn_key, {})
        interface = cfg.get("interface", "")
        serial_baudrate = cfg.get("baudrate", 115200)
        bitrate = cfg.get("bitrate", 500000)

        while conn_key in self._buses:
            try:
                msg: Optional[Message] = await loop.run_in_executor(
                    None, bus.recv, 0.1
                )
            except Exception as exc:
                log.warning("recv error on %s: %s", conn_key, exc)
                await asyncio.sleep(0.1)
                continue

            if msg is None:
                _consecutive_none += 1
                if (
                    _consecutive_none == 300
                    and interface == "slcan"
                ):
                    log.warning(
                        "slcan %s: no frames received in ~30 s. "
                        "Check: (1) CAN bitrate matches the bus (%d bps), "
                        "(2) serial baud rate matches adapter (current: %d).",
                        conn_key, bitrate, serial_baudrate
                    )
                continue

            _consecutive_none = 0
            msg.timestamp = time.monotonic() - self._open_time
            msg.channel = conn_key  # Override python-can channel with our unique identifier

            for cb in list(self._frame_callbacks):
                try:
                    cb(msg)
                except Exception as exc:
                    log.warning("Frame callback error on connection %s: %s", conn_key, exc)

        log.debug("Reader loop exited for connection %s.", conn_key)


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
            "  1. pip install libusb\n"
            "  2. If that still fails, download libusb-1.0.dll from https://libusb.info\n"
            "     and place it next to python.exe\n"
            "     (e.g. C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python312\\)\n"
        ) from exc


def _open_bus(
    interface: InterfaceType,
    channel: str,
    bitrate: int,
    index: int,
    serial_baudrate: int = 115200,
) -> can.BusABC:
    if interface == "gs_usb":
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