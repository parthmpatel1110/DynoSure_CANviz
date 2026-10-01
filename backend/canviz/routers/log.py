"""
canviz/routers/log.py
---------------------
Session logging endpoints.

POST /log/start  — begin recording frames to .asc and .csv
POST /log/stop   — stop recording; returns download paths
GET  /log/download/{filename} — serve the recorded file

Frames are written asynchronously via aiofiles so the event loop
is never blocked by disk I/O.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import aiofiles
import can
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from canviz.bus import bus_manager

log = logging.getLogger("canviz.log")
router = APIRouter(prefix="/log", tags=["logging"])

# Where logs are written — will be created if it doesn't exist
LOG_DIR = Path("logs")

_session: LogSession | None = None


class LogSession:
    def __init__(self, base: str) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        self.base    = base
        self.asc_path = LOG_DIR / f"{base}.asc"
        self.csv_path = LOG_DIR / f"{base}.csv"
        self.mf4_path = LOG_DIR / f"{base}.mf4"
        self._mf4_writer: can.MF4Writer | None = None
        try:
            self._mf4_writer = can.MF4Writer(str(self.mf4_path))
        except Exception as exc:
            log.warning("Could not initialize MF4Writer: %s", exc)

        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._start_time = time.monotonic()
        self._count = 0

    async def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(
            self._writer_loop(), name="log-writer"
        )
        bus_manager.add_frame_callback(self._on_frame)
        log.info("Logging started → %s / %s / %s", self.asc_path, self.csv_path, self.mf4_path)

    async def stop(self) -> dict:
        bus_manager.remove_frame_callback(self._on_frame)
        await self._queue.put(None)  # sentinel
        if self._task:
            await self._task
        log.info("Logging stopped. %d frames written.", self._count)
        res = {
            "frames":   self._count,
            "asc_file": str(self.asc_path),
            "csv_file": str(self.csv_path),
        }
        if self.mf4_path.exists():
            res["mf4_file"] = str(self.mf4_path)
        return res

    def _on_frame(self, msg) -> None:
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    async def _writer_loop(self) -> None:
        async with aiofiles.open(self.asc_path, "w") as asc_f, \
                   aiofiles.open(self.csv_path, "w") as csv_f:

            # ASC header
            await asc_f.write(f"date {time.strftime('%a %b %d %H:%M:%S %Y')}\n")
            await asc_f.write("base hex  timestamps absolute\n")
            await asc_f.write("no internal events logged\n")

            # CSV header
            await csv_f.write("timestamp,id,dlc,data,is_extended_id,is_fd,bitrate_switch\n")

            while True:
                msg = await self._queue.get()
                if msg is None:
                    break

                ts   = round(msg.timestamp, 6)
                id_s = f"{msg.arbitration_id:X}"
                data = " ".join(f"{b:02x}" for b in msg.data)
                ext  = "1" if msg.is_extended_id else "0"
                is_fd = bool(getattr(msg, "is_fd", False))
                brs   = bool(getattr(msg, "bitrate_switch", False))

                # ASC line: CAN FD vs Classic CAN
                if is_fd:
                    brs_flag = "1" if brs else "0"
                    esi_flag = "1" if getattr(msg, "error_state_indicator", False) else "0"
                    data_len = len(msg.data)
                    await asc_f.write(
                        f"   {ts:.6f} CANFD 1 Rx {id_s}   {brs_flag} {esi_flag} {data_len:2d} {msg.dlc:2d}  {data}\n"
                    )
                else:
                    await asc_f.write(
                        f"   {ts:.6f} 1  {id_s}  Rx   d {msg.dlc}  {data}\n"
                    )

                # CSV line
                await csv_f.write(
                    f"{ts},{id_s},{msg.dlc},{data.replace(' ', '')},{ext},{1 if is_fd else 0},{1 if brs else 0}\n"
                )

                # MF4 binary recording
                if self._mf4_writer is not None:
                    try:
                        self._mf4_writer.on_message_received(msg)
                    except Exception as exc:
                        log.debug("MF4 write failed: %s", exc)

                self._count += 1

            if self._mf4_writer is not None:
                try:
                    self._mf4_writer.stop()
                except Exception as exc:
                    log.warning("MF4 writer close error: %s", exc)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/start")
async def log_start():
    global _session
    if _session is not None:
        raise HTTPException(status_code=400, detail="Already logging. Call /log/stop first.")
    if not bus_manager.connected:
        raise HTTPException(status_code=400, detail="Not connected. Call /connect first.")

    base = time.strftime("canviz_%Y%m%d_%H%M%S")
    _session = LogSession(base)
    await _session.start()
    return {"ok": True, "base": base}


@router.post("/stop")
async def log_stop():
    global _session
    if _session is None:
        raise HTTPException(status_code=400, detail="Not currently logging.")
    result = await _session.stop()
    _session = None
    return {"ok": True, **result}


@router.get("/download/{filename}")
async def log_download(filename: str):
    # Sanitise — only allow files inside LOG_DIR
    target = (LOG_DIR / filename).resolve()
    if not str(target).startswith(str(LOG_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    media_type = "application/octet-stream"
    if filename.endswith(".asc") or filename.endswith(".csv"):
        media_type = "text/plain"
    return FileResponse(path=target, filename=filename, media_type=media_type)
