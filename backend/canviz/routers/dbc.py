"""
canviz/routers/dbc.py
---------------------
DBC file management endpoints.

POST   /dbc/load      — upload and parse a DBC file
GET    /dbc/messages  — list all decoded message definitions
DELETE /dbc           — unload the current DBC
"""

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from canviz.dbc_store import dbc_store

router = APIRouter(prefix="/dbc", tags=["dbc"])


@router.post("/load")
async def load_dbc(file: UploadFile = File(...)):
    content = await file.read()
    if not file.filename or not file.filename.lower().endswith(".dbc"):
        raise HTTPException(status_code=400, detail="File must have a .dbc extension.")
    try:
        summary = dbc_store.load(content, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return summary


@router.get("/messages")
async def get_messages():
    if not dbc_store.loaded:
        return {"loaded": False, "messages": []}
    return {"loaded": True, "filename": dbc_store.path, "messages": dbc_store.messages_list()}

class EncodeRequest(BaseModel):
    message_id: int
    signals: dict[str, float]


@router.post("/encode")
async def encode_dbc_message(req: EncodeRequest):
    if not dbc_store.loaded:
        raise HTTPException(status_code=400, detail="No DBC file loaded.")
    try:
        assert dbc_store._db is not None
        # get_message_by_frame_id raises KeyError if not found
        message = dbc_store._db.get_message_by_frame_id(req.message_id)
        # Encode signal dictionary to bytes
        encoded = message.encode(req.signals, scaling=True, padding=True)
        return {"data": list(encoded), "dlc": len(encoded)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Message ID {req.message_id} not found in DBC.")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("")
async def unload_dbc():
    dbc_store.unload()
    return {"ok": True, "message": "DBC unloaded."}
