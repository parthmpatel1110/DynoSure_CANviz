"""
canviz/routers/connect.py
-------------------------
REST endpoints for bus lifecycle management.

POST /connect    - open the CAN interface and start streaming
POST /disconnect - stop the interface cleanly
GET  /status     - current connection state
"""

from typing import Optional
from fastapi import APIRouter, HTTPException

from canviz.bus import bus_manager
from canviz.config import settings
from canviz.models import ConnectRequest, ConnectionStatus, ActiveConnection
from canviz.ws_broadcaster import broadcaster
from canviz.stats_store import stats

router = APIRouter(tags=["connection"])


@router.post("/connect", response_model=ConnectionStatus)
async def connect(req: ConnectRequest):
    baudrate = req.baudrate
    if req.interface == "gs_usb":
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    bus_manager.add_frame_callback(broadcaster.on_frame)
    stats.on_connect(bitrate=req.bitrate)
    broadcaster.start()

    return _status()


@router.post("/disconnect", response_model=ConnectionStatus)
async def disconnect(connection_id: Optional[str] = None):
    if connection_id:
        await bus_manager.disconnect(connection_id)
        if not bus_manager.connected:
            bus_manager.remove_frame_callback(broadcaster.on_frame)
            stats.on_disconnect()
            broadcaster.clear_queue()
    else:
        bus_manager.remove_frame_callback(broadcaster.on_frame)
        stats.on_disconnect()
        broadcaster.clear_queue()
        await bus_manager.disconnect()
    return _status()


@router.get("/status", response_model=ConnectionStatus)
async def status():
    return _status()


def _status() -> ConnectionStatus:
    conns = []
    for conn_key, cfg in bus_manager._configs.items():
        conns.append(
            ActiveConnection(
                id=conn_key,
                interface=cfg.get("interface", ""),
                channel=cfg.get("channel", ""),
                bitrate=cfg.get("bitrate", 0),
                index=cfg.get("index", 0),
            )
        )

    first_conn = conns[0] if conns else None
    return ConnectionStatus(
        connected=len(conns) > 0,
        interface=first_conn.interface if first_conn else "",
        channel=first_conn.channel if first_conn else "",
        bitrate=first_conn.bitrate if first_conn else 500000,
        index=first_conn.index if first_conn else 0,
        error=bus_manager.error,
        connections=conns,
    )