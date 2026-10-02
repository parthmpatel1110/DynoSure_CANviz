"""
canviz/flasher/base.py
----------------------
Core interfaces and base classes for the Universal CAN Flashing tool.
Users can programmatically define custom flashing protocols by inheriting from
BaseFlashingProtocol and implementing the lifecycle methods.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

import can


class FlasherAbortedException(Exception):
    """Raised when the user aborts an ongoing flashing operation."""


@dataclass
class ProtocolOption:
    """Configurable parameter definition exposed to the user interface."""
    key: str
    label: str
    type: str  # "string" | "number" | "boolean" | "select"
    default: Any
    options: list[str] | None = None
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "options": self.options or [],
            "description": self.description,
        }


class FlashContext:
    """
    Execution context provided to a protocol's flash() method.
    Provides helpers for sending, receiving, logging, updating progress,
    and handling abort requests.
    """

    def __init__(
        self,
        bus: can.BusABC,
        tx_id: int,
        rx_id: int,
        is_extended_id: bool,
        is_fd: bool,
        bitrate_switch: bool,
        base_address: int,
        chunk_size: int,
        firmware_bytes: bytes,
        options: dict[str, Any],
        log_callback: Callable[[str, str], None],
        stage_callback: Callable[[str], None],
        progress_callback: Callable[[int, int], None],
        abort_check: Callable[[], bool],
    ) -> None:
        self.bus = bus
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.is_extended_id = is_extended_id
        self.is_fd = is_fd
        self.bitrate_switch = bitrate_switch
        self.base_address = base_address
        self.chunk_size = chunk_size
        self.firmware_bytes = firmware_bytes
        self.options = options
        self._log_cb = log_callback
        self._stage_cb = stage_callback
        self._progress_cb = progress_callback
        self._abort_check = abort_check

    def check_abort(self) -> None:
        """Raise FlasherAbortedException if the user requested abort."""
        if self._abort_check():
            raise FlasherAbortedException("Flashing operation was aborted by user.")

    def log(self, message: str, level: str = "info") -> None:
        """
        Emit a diagnostic log entry to the flasher console.
        level can be: "info", "debug", "warning", "error", "tx", "rx", "success"
        """
        self._log_cb(message, level)

    def set_stage(self, stage: str) -> None:
        """Update current flashing stage: e.g. CONNECTING, ERASING, FLASHING, VERIFYING, RESETTING."""
        self._stage_cb(stage)

    def update_progress(self, bytes_done: int, total_bytes: int | None = None) -> None:
        """Update progress metrics."""
        tot = total_bytes if total_bytes is not None else len(self.firmware_bytes)
        self._progress_cb(bytes_done, tot)

    async def send_frame(
        self,
        arb_id: int | None = None,
        data: list[int] | bytes = b"",
        is_extended_id: bool | None = None,
        is_fd: bool | None = None,
        bitrate_switch: bool | None = None,
    ) -> None:
        """Send a single CAN or CAN FD frame."""
        self.check_abort()
        target_id = self.tx_id if arb_id is None else arb_id
        ext = self.is_extended_id if is_extended_id is None else is_extended_id
        fd = self.is_fd if is_fd is None else is_fd
        brs = self.bitrate_switch if bitrate_switch is None else bitrate_switch

        data_bytes = bytes(data)
        msg = can.Message(
            arbitration_id=target_id,
            data=data_bytes,
            is_extended_id=ext,
            is_fd=fd,
            bitrate_switch=brs,
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.bus.send, msg)

        hex_data = " ".join(f"{b:02X}" for b in data_bytes)
        self.log(f"Tx 0x{target_id:03X}: {hex_data}", level="tx")

    async def recv_frame(
        self,
        rx_id: int | None = None,
        timeout: float = 2.0,
    ) -> can.Message | None:
        """Wait for a CAN frame with the expected response ID."""
        self.check_abort()
        target_rx = self.rx_id if rx_id is None else rx_id
        loop = asyncio.get_event_loop()
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            self.check_abort()
            remaining = max(0.01, min(0.1, deadline - time.monotonic()))
            try:
                msg = await loop.run_in_executor(None, self.bus.recv, remaining)
                if msg is not None:
                    if target_rx is None or msg.arbitration_id == target_rx:
                        hex_data = " ".join(f"{b:02X}" for b in msg.data)
                        self.log(f"Rx 0x{msg.arbitration_id:03X}: {hex_data}", level="rx")
                        return msg
            except Exception as exc:  # noqa: BLE001
                self.log(f"Recv exception: {exc}", level="error")
                await asyncio.sleep(0.05)

        return None

    async def send_and_recv(
        self,
        tx_id: int | None = None,
        data: list[int] | bytes = b"",
        rx_id: int | None = None,
        timeout: float = 2.0,
        is_extended_id: bool | None = None,
        is_fd: bool | None = None,
        bitrate_switch: bool | None = None,
    ) -> bytes | None:
        """Convenience method: sends a frame and awaits a matching response."""
        await self.send_frame(
            arb_id=tx_id,
            data=data,
            is_extended_id=is_extended_id,
            is_fd=is_fd,
            bitrate_switch=bitrate_switch,
        )
        resp = await self.recv_frame(rx_id=rx_id, timeout=timeout)
        if resp is not None:
            return bytes(resp.data)
        return None


class BaseFlashingProtocol(ABC):
    """
    Abstract Base Class for all CAN Flashing Protocols.
    Subclass this to create user-specific flashing routines programmatically.
    """

    id: str = "base_protocol"
    name: str = "Base Flashing Protocol"
    description: str = "Base protocol template"
    default_tx_id: int = 0x7E0
    default_rx_id: int = 0x7E8
    is_extended_id: bool = False
    supports_fd: bool = True
    default_chunk_size: int = 64
    options_schema: list[ProtocolOption] = []

    @classmethod
    def get_info(cls) -> dict[str, Any]:
        """Return protocol metadata serialisable to JSON."""
        raw_opts = getattr(cls, "options_schema", [])
        if not isinstance(raw_opts, (list, tuple)):
            raw_opts = []
        return {
            "id": cls.id,
            "name": cls.name,
            "description": cls.description,
            "version": getattr(cls, "version", "1.0.0"),
            "author": getattr(cls, "author", "CANviz"),
            "is_custom": getattr(cls, "is_custom", False),
            "default_tx_id": f"0x{cls.default_tx_id:X}",
            "default_rx_id": f"0x{cls.default_rx_id:X}",
            "is_extended_id": cls.is_extended_id,
            "supports_fd": cls.supports_fd,
            "default_chunk_size": cls.default_chunk_size,
            "supported_actions": getattr(cls, "supported_actions", ["erase", "verify", "reset_ecu"]),
            "options": [opt.as_dict() for opt in raw_opts if hasattr(opt, "as_dict")],
        }

    @abstractmethod
    async def flash(self, ctx: FlashContext) -> None:
        """
        Execute full flashing lifecycle:
        1. Handshake / Enter bootloader mode
        2. Unlock / Security Access
        3. Erase memory
        4. Transfer firmware chunks
        5. Verify checksum
        6. Reset ECU / Run application
        """
        raise NotImplementedError

    async def erase(self, ctx: FlashContext) -> None:
        """Optional standalone memory erase."""
        ctx.log("Standalone erase not implemented for this protocol.", level="warning")

    async def verify(self, ctx: FlashContext) -> None:
        """Optional standalone checksum verification."""
        ctx.log("Standalone verify not implemented for this protocol.", level="warning")

    async def reset_ecu(self, ctx: FlashContext) -> None:
        """Optional standalone ECU reset / Go command."""
        ctx.log("Standalone reset not implemented for this protocol.", level="warning")
