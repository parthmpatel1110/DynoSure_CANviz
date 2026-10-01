"""
canviz/routers/flasher.py
-------------------------
REST and WebSocket API endpoints for the Universal CAN Flashing Tool.
"""

from __future__ import annotations

import base64
import inspect
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from canviz.flasher import flasher_manager, parse_firmware

router = APIRouter(prefix="/flasher", tags=["flasher"])


# ── Request / Response Models ────────────────────────────────────────────────

class StartFlashRequest(BaseModel):
    protocol_id: str
    firmware_base64: str            # Base64-encoded firmware binary
    tx_id: int | str                # Integer or hex string e.g. "0x7E0"
    rx_id: int | str                # Integer or hex string e.g. "0x7E8"
    base_address: int | str = 0x08000000
    is_extended_id: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    chunk_size: int = 64
    options: dict[str, Any] = {}


class ActionRequest(BaseModel):
    action: str                     # "erase" | "verify" | "reset_ecu"
    protocol_id: str
    tx_id: int | str
    rx_id: int | str
    base_address: int | str = 0x08000000
    is_extended_id: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    options: dict[str, Any] = {}


class UploadCodeRequest(BaseModel):
    filename: str
    code: str


def _parse_id(val: int | str) -> int:
    if isinstance(val, int):
        return val
    s = str(val).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


# ── Protocol Management Endpoints ────────────────────────────────────────────

@router.get("/protocols")
async def list_protocols():
    """List all registered flashing protocols with metadata and options schemas."""
    return {"protocols": flasher_manager.list_protocols()}


@router.get("/protocols/template")
async def get_protocol_template():
    """Return the source code of the starter template for creating custom protocols."""
    template_path = Path(__file__).parent.parent / "flasher" / "protocols" / "template.py"
    if template_path.exists():
        return {"code": template_path.read_text(encoding="utf-8")}
    return {"code": "# Protocol template not found."}


@router.get("/protocols/{protocol_id}/code")
async def get_protocol_code(protocol_id: str):
    """Inspect the Python source code of a registered protocol."""
    proto = flasher_manager.get_protocol(protocol_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Protocol not found.")
    try:
        source = inspect.getsource(proto.__class__)
        return {"id": protocol_id, "code": source}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not retrieve source: {exc}")


@router.post("/protocols/upload")
async def upload_protocol_file(file: UploadFile = File(...)):
    """Upload a custom Python protocol (.py) script and register it dynamically."""
    if not (file.filename or "").endswith(".py"):
        raise HTTPException(status_code=400, detail="Only Python (.py) files are accepted.")

    content = (await file.read()).decode("utf-8", errors="replace")
    try:
        res = flasher_manager.load_protocol_from_code(content, filename=file.filename or "custom.py")
        return {"ok": True, "message": "Protocol registered successfully", **res}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/protocols/submit_code")
async def submit_protocol_code(req: UploadCodeRequest):
    """Directly submit Python protocol code as JSON text."""
    try:
        res = flasher_manager.load_protocol_from_code(req.code, filename=req.filename)
        return {"ok": True, "message": "Protocol registered successfully", **res}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


# ── Firmware Parsing Endpoint ────────────────────────────────────────────────

@router.post("/firmware/parse")
async def parse_firmware_file(file: UploadFile = File(...)):
    """
    Parse an uploaded .bin, .hex, or .srec firmware file.
    Returns detected format, byte size, base address, and checksums.
    """
    filename = file.filename or "firmware.bin"
    content = await file.read()
    try:
        info = parse_firmware(filename, content)
        b64_data = base64.b64encode(info.data).decode("ascii")
        return {
            "ok": True,
            **info.as_dict(),
            "firmware_base64": b64_data,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Failed to parse firmware: {exc}")


# ── Execution Controls ───────────────────────────────────────────────────────

@router.post("/start")
async def start_flash(req: StartFlashRequest):
    """Initiate full firmware flashing operation."""
    try:
        firmware_bytes = base64.b64decode(req.firmware_base64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid base64 firmware: {exc}")

    try:
        tx_id = _parse_id(req.tx_id)
        rx_id = _parse_id(req.rx_id)
        base_addr = _parse_id(req.base_address)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid CAN or memory ID: {exc}")

    try:
        flasher_manager.start_flash(
            protocol_id=req.protocol_id,
            firmware_bytes=firmware_bytes,
            tx_id=tx_id,
            rx_id=rx_id,
            base_address=base_addr,
            is_extended_id=req.is_extended_id,
            is_fd=req.is_fd,
            bitrate_switch=req.bitrate_switch,
            chunk_size=req.chunk_size,
            options=req.options,
        )
        return {"ok": True, "message": "Flashing started"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/action")
async def run_action(req: ActionRequest):
    """Run individual action: erase, verify, or reset_ecu."""
    try:
        tx_id = _parse_id(req.tx_id)
        rx_id = _parse_id(req.rx_id)
        base_addr = _parse_id(req.base_address)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid ID: {exc}")

    try:
        flasher_manager.start_action(
            action=req.action,
            protocol_id=req.protocol_id,
            tx_id=tx_id,
            rx_id=rx_id,
            base_address=base_addr,
            is_extended_id=req.is_extended_id,
            is_fd=req.is_fd,
            bitrate_switch=req.bitrate_switch,
            options=req.options,
        )
        return {"ok": True, "message": f"Action {req.action} started"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/abort")
async def abort_flash():
    """Cancel the active flashing operation."""
    flasher_manager.abort()
    return {"ok": True, "message": "Abort signal dispatched"}


@router.get("/status")
async def get_status():
    """Get current status, progress, speed, and ETA."""
    return flasher_manager.get_status()


@router.get("/logs")
async def get_logs():
    """Get all stored diagnostic log messages for this session."""
    return {"logs": flasher_manager.get_logs()}


@router.post("/logs/clear")
async def clear_logs():
    """Clear diagnostic logs."""
    flasher_manager.clear_logs()
    return {"ok": True}


# ── WebSocket Telemetry & Log Streaming ──────────────────────────────────────

@router.websocket("/ws")
async def flasher_websocket(websocket: WebSocket):
    """
    WebSocket endpoint streaming live flashing progress, speed, ETA,
    stage transitions, and colored diagnostic log messages.
    """
    await websocket.accept()
    queue = asyncio.Queue(maxsize=1000)
    flasher_manager.subscribe(queue)

    # Immediately push current status and backlog of logs
    try:
        await websocket.send_json({"type": "init", "status": flasher_manager.get_status(), "logs": flasher_manager.get_logs()})
    except Exception:  # noqa: BLE001
        flasher_manager.unsubscribe(queue)
        return

    try:
        while True:
            # Drain queue and send to WebSocket client
            event = await queue.get()
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        flasher_manager.unsubscribe(queue)
