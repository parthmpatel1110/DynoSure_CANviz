"""
canviz/routers/frames.py
------------------------
WebSocket endpoint for live frame streaming + REST send endpoint.

GET  /ws/frames  — WebSocket, streams every received CAN frame as JSON
POST /send       — transmit a manually crafted frame onto the bus
"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from canviz.bus import bus_manager
from canviz.models import SendFrameRequest
from canviz.stats_store import stats
from canviz.ws_broadcaster import broadcaster

router = APIRouter(tags=["frames"])


@router.websocket("/ws/frames")
async def ws_frames(websocket: WebSocket):
    await broadcaster.register(websocket)
    try:
        # Keep alive — we only need to detect disconnection
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(websocket)


@router.post("/send")
async def send_frame(req: SendFrameRequest):
    if not bus_manager.connected:
        raise HTTPException(status_code=400, detail="Not connected. Call /connect first.")
    max_len = 64 if req.is_fd else 8
    if len(req.data) > max_len:
        proto_name = "CAN FD" if req.is_fd else "CAN 2.0"
        raise HTTPException(status_code=400, detail=f"{proto_name} data max {max_len} bytes.")
    try:
        await bus_manager.send(
            arbitration_id=req.id,
            data=req.data,
            is_extended_id=req.is_extended_id,
            is_fd=req.is_fd,
            bitrate_switch=req.bitrate_switch,
        )
        stats.on_tx(len(req.data))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "ok": True,
        "id": hex(req.id),
        "data": req.data,
        "is_fd": req.is_fd,
        "bitrate_switch": req.bitrate_switch,
    }
