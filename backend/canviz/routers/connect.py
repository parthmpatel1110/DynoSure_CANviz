"""
canviz/routers/connect.py
-------------------------
REST endpoints for bus lifecycle management.

POST /connect    - open the CAN interface and start streaming
POST /disconnect - stop the interface cleanly
GET  /status     - current connection state
"""

from fastapi import APIRouter, HTTPException

from canviz.bus import bus_manager
from canviz.config import settings
from canviz.models import ConnectionStatus, ConnectRequest
from canviz.stats_store import stats
from canviz.ws_broadcaster import broadcaster

router = APIRouter(tags=["connection"])


@router.post("/connect", response_model=ConnectionStatus)
async def connect(req: ConnectRequest):
    baudrate = req.baudrate
    if req.interface in ("gs_usb", "dynosure-slcan"):
        index   = int(req.channel) if req.channel != "" and str(req.channel).isdigit() else req.index
        channel = ""
    elif req.interface == "kvaser":
        index   = req.index
        channel = int(req.channel) if req.channel not in ("", None) and str(req.channel).isdigit() else req.index
    elif req.interface == "pcan":
        index   = req.index
        channel = str(req.channel) if req.channel else "PCAN_USBBUS1"
    elif req.interface in ("slcan", "seeedstudio"):
        index   = req.index
        channel = str(req.channel)
    else:
        index   = req.index
        channel = str(req.channel)

    try:
        await bus_manager.connect(
            interface=req.interface,
            channel=channel,
            bitrate=req.bitrate,
            index=index,
            baudrate=baudrate
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))

    bus_manager.add_frame_callback(broadcaster.on_frame)
    stats.on_connect(bitrate=req.bitrate)
    broadcaster.start()

    return _status()


@router.post("/disconnect", response_model=ConnectionStatus)
async def disconnect():
    bus_manager.remove_frame_callback(broadcaster.on_frame)
    stats.on_disconnect()
    broadcaster.clear_queue()
    await bus_manager.disconnect()
    return _status()


@router.get("/status", response_model=ConnectionStatus)
async def status():
    return _status()


def _status() -> ConnectionStatus:
    return ConnectionStatus(
        connected=bus_manager.connected,
        interface=settings.interface,
        channel=settings.channel,
        bitrate=settings.bitrate,
        index=settings.index,
        error=bus_manager.error,
    )