"""
canviz/flasher/manager.py
-------------------------
Manages the lifecycle of CAN flashing protocols, custom protocol loading,
and background execution of firmware flashing tasks with WebSocket progress streaming.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import logging
import pkgutil
import time
from pathlib import Path
from typing import Any, Type

from canviz.bus import bus_manager
from canviz.flasher.base import (
    BaseFlashingProtocol,
    FlashContext,
    FlasherAbortedException,
)

log = logging.getLogger("canviz.flasher")


class FlasherManager:
    def __init__(self) -> None:
        self._protocols: dict[str, BaseFlashingProtocol] = {}
        self._active_task: asyncio.Task | None = None
        self._abort_flag: bool = False
        self._subscribers: list[asyncio.Queue] = []

        # State metrics
        self._status: str = "idle"  # idle | connecting | erasing | flashing | verifying | resetting | completed | error | aborted
        self._stage: str = "IDLE"
        self._progress: float = 0.0
        self._bytes_transferred: int = 0
        self._total_bytes: int = 0
        self._speed_kbps: float = 0.0
        self._eta_seconds: float = 0.0
        self._error_message: str | None = None
        self._start_time: float = 0.0
        self._logs: list[dict[str, Any]] = []

    # ── Protocol Registry ────────────────────────────────────────────────────

    def register(self, protocol_cls_or_instance: Type[BaseFlashingProtocol] | BaseFlashingProtocol) -> None:
        """Register a protocol class or instance."""
        if inspect.isclass(protocol_cls_or_instance):
            instance = protocol_cls_or_instance()
        else:
            instance = protocol_cls_or_instance

        if not isinstance(instance, BaseFlashingProtocol):
            raise TypeError("Protocol must inherit from BaseFlashingProtocol")

        self._protocols[instance.id] = instance
        log.info("Registered flashing protocol: %s (%s)", instance.name, instance.id)

    def get_protocol(self, protocol_id: str) -> BaseFlashingProtocol | None:
        return self._protocols.get(protocol_id)

    def list_protocols(self) -> list[dict[str, Any]]:
        return [proto.get_info() for proto in self._protocols.values()]

    def discover_protocols(self) -> None:
        """Automatically import and register all protocol modules in the protocols directory."""
        import canviz.flasher.protocols as proto_pkg

        pkg_path = Path(proto_pkg.__file__).parent
        for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
            try:
                mod = importlib.import_module(f"canviz.flasher.protocols.{module_name}")
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        inspect.isclass(attr)
                        and issubclass(attr, BaseFlashingProtocol)
                        and attr is not BaseFlashingProtocol
                    ):
                        self.register(attr)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to load protocol module %s: %s", module_name, exc)

    def load_protocol_from_code(self, code_str: str, filename: str = "custom_protocol.py") -> dict[str, Any]:
        """
        Dynamically compile, validate, and register a user-uploaded protocol script.
        """
        # Validate AST first
        try:
            tree = ast.parse(code_str, filename=filename)
        except SyntaxError as exc:
            raise ValueError(f"Syntax error in protocol script: {exc}") from exc

        # Execute in isolated module dictionary
        mod_globals = {
            "__name__": f"canviz.flasher.protocols.user_{Path(filename).stem}",
            "__file__": filename,
        }
        try:
            exec(compile(tree, filename=filename, mode="exec"), mod_globals)  # noqa: S102
        except Exception as exc:
            raise RuntimeError(f"Error executing protocol code: {exc}") from exc

        registered = []
        for attr_name, attr in mod_globals.items():
            if (
                inspect.isclass(attr)
                and issubclass(attr, BaseFlashingProtocol)
                and attr is not BaseFlashingProtocol
            ):
                self.register(attr)
                registered.append(attr.id)

        if not registered:
            raise ValueError("No subclasses of BaseFlashingProtocol found in uploaded code.")

        # Persist to protocols directory so it is preserved
        try:
            import canviz.flasher.protocols as proto_pkg
            pkg_path = Path(proto_pkg.__file__).parent
            save_path = pkg_path / f"user_{Path(filename).stem}.py"
            save_path.write_text(code_str, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not persist uploaded protocol to disk: %s", exc)

        return {"registered": registered, "filename": filename}

    # ── State and WebSocket Subscriptions ────────────────────────────────────

    def subscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.append(queue)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except Exception:  # noqa: BLE001
                pass

    def _add_log(self, text: str, level: str = "info") -> None:
        entry = {
            "timestamp": round(time.time(), 3),
            "level": level,
            "message": text,
        }
        self._logs.append(entry)
        if len(self._logs) > 5000:
            self._logs.pop(0)

        self._broadcast({"type": "log", **entry})

    def _update_stage(self, stage: str) -> None:
        self._stage = stage
        self._broadcast({"type": "stage", "stage": stage})

    def _update_progress(self, bytes_done: int, total_bytes: int) -> None:
        self._bytes_transferred = bytes_done
        self._total_bytes = total_bytes
        pct = (bytes_done / total_bytes * 100.0) if total_bytes > 0 else 0.0
        self._progress = round(pct, 1)

        now = time.monotonic()
        elapsed = now - self._start_time
        if elapsed > 0.5:
            self._speed_kbps = round((bytes_done / 1024.0) / elapsed, 1)
            remaining_bytes = max(0, total_bytes - bytes_done)
            if self._speed_kbps > 0:
                self._eta_seconds = round((remaining_bytes / 1024.0) / self._speed_kbps, 1)

        self._broadcast({
            "type": "progress",
            "progress": self._progress,
            "bytes_transferred": self._bytes_transferred,
            "total_bytes": self._total_bytes,
            "speed_kbps": self._speed_kbps,
            "eta_seconds": self._eta_seconds,
            "stage": self._stage,
        })

    def get_status(self) -> dict[str, Any]:
        return {
            "status": self._status,
            "stage": self._stage,
            "progress": self._progress,
            "bytes_transferred": self._bytes_transferred,
            "total_bytes": self._total_bytes,
            "speed_kbps": self._speed_kbps,
            "eta_seconds": self._eta_seconds,
            "error": self._error_message,
            "active": self._status in ("connecting", "erasing", "flashing", "verifying", "resetting"),
        }

    def get_logs(self) -> list[dict[str, Any]]:
        return list(self._logs)

    def clear_logs(self) -> None:
        self._logs.clear()
        self._broadcast({"type": "clear_logs"})

    # ── Flashing Execution ───────────────────────────────────────────────────

    def start_flash(
        self,
        protocol_id: str,
        firmware_bytes: bytes,
        tx_id: int,
        rx_id: int,
        base_address: int,
        is_extended_id: bool = False,
        is_fd: bool = False,
        bitrate_switch: bool = False,
        chunk_size: int = 64,
        options: dict[str, Any] | None = None,
    ) -> None:
        if self._active_task and not self._active_task.done():
            raise RuntimeError("A flashing operation is already in progress.")

        if not bus_manager.connected or bus_manager.bus is None:
            raise RuntimeError("CAN bus is not connected. Connect via Connection tab first.")

        proto = self.get_protocol(protocol_id)
        if not proto:
            raise ValueError(f"Unknown flashing protocol: {protocol_id}")

        self._abort_flag = False
        self._status = "connecting"
        self._stage = "INITIALIZING"
        self._progress = 0.0
        self._bytes_transferred = 0
        self._total_bytes = len(firmware_bytes)
        self._speed_kbps = 0.0
        self._eta_seconds = 0.0
        self._error_message = None
        self._start_time = time.monotonic()
        self._logs.clear()

        ctx = FlashContext(
            bus=bus_manager.bus,
            tx_id=tx_id,
            rx_id=rx_id,
            is_extended_id=is_extended_id,
            is_fd=is_fd,
            bitrate_switch=bitrate_switch,
            base_address=base_address,
            chunk_size=chunk_size,
            firmware_bytes=firmware_bytes,
            options=options or {},
            log_callback=self._add_log,
            stage_callback=self._update_stage,
            progress_callback=self._update_progress,
            abort_check=lambda: self._abort_flag,
        )

        async def _runner():
            try:
                self._broadcast({"type": "status_change", "status": "running"})
                self._add_log(f"Starting {proto.name} (Tx: 0x{tx_id:X}, Rx: 0x{rx_id:X}, FD: {is_fd})")
                await proto.flash(ctx)
                self._status = "completed"
                self._stage = "COMPLETED"
                self._progress = 100.0
                elapsed = time.monotonic() - self._start_time
                self._add_log(f"Flashing finished successfully in {elapsed:.1f}s!", level="success")
                self._broadcast({"type": "complete", "success": True, "elapsed": round(elapsed, 1)})
            except FlasherAbortedException:
                self._status = "aborted"
                self._stage = "ABORTED"
                self._add_log("Flashing operation aborted by user.", level="warning")
                self._broadcast({"type": "aborted"})
            except Exception as exc:  # noqa: BLE001
                self._status = "error"
                self._stage = "ERROR"
                self._error_message = str(exc)
                self._add_log(f"Flashing failed: {exc}", level="error")
                self._broadcast({"type": "error", "error": str(exc)})

        loop = asyncio.get_event_loop()
        self._active_task = loop.create_task(_runner(), name="can-flasher")

    def start_action(
        self,
        action: str,  # "erase" | "verify" | "reset_ecu"
        protocol_id: str,
        tx_id: int,
        rx_id: int,
        base_address: int = 0x08000000,
        firmware_bytes: bytes = b"",
        is_extended_id: bool = False,
        is_fd: bool = False,
        bitrate_switch: bool = False,
        options: dict[str, Any] | None = None,
    ) -> None:
        if self._active_task and not self._active_task.done():
            raise RuntimeError("An operation is already in progress.")

        if not bus_manager.connected or bus_manager.bus is None:
            raise RuntimeError("CAN bus is not connected.")

        proto = self.get_protocol(protocol_id)
        if not proto:
            raise ValueError(f"Unknown protocol: {protocol_id}")

        self._abort_flag = False
        self._status = "running"
        self._error_message = None

        ctx = FlashContext(
            bus=bus_manager.bus,
            tx_id=tx_id,
            rx_id=rx_id,
            is_extended_id=is_extended_id,
            is_fd=is_fd,
            bitrate_switch=bitrate_switch,
            base_address=base_address,
            chunk_size=64,
            firmware_bytes=firmware_bytes,
            options=options or {},
            log_callback=self._add_log,
            stage_callback=self._update_stage,
            progress_callback=self._update_progress,
            abort_check=lambda: self._abort_flag,
        )

        async def _runner():
            try:
                self._broadcast({"type": "status_change", "status": "running"})
                if action == "erase":
                    await proto.erase(ctx)
                elif action == "verify":
                    await proto.verify(ctx)
                elif action == "reset_ecu":
                    await proto.reset_ecu(ctx)
                else:
                    raise ValueError(f"Unknown action: {action}")
                self._status = "completed"
                self._broadcast({"type": "complete", "success": True})
            except FlasherAbortedException:
                self._status = "aborted"
                self._broadcast({"type": "aborted"})
            except Exception as exc:  # noqa: BLE001
                self._status = "error"
                self._error_message = str(exc)
                self._add_log(f"Action {action} failed: {exc}", level="error")
                self._broadcast({"type": "error", "error": str(exc)})

        loop = asyncio.get_event_loop()
        self._active_task = loop.create_task(_runner(), name=f"flasher-{action}")

    def abort(self) -> None:
        if self._active_task and not self._active_task.done():
            self._abort_flag = True
            self._add_log("Abort requested by user...", level="warning")


# Global Flasher Manager singleton
flasher_manager = FlasherManager()
