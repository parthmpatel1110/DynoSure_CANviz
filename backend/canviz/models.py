"""
canviz/models.py
----------------
Pydantic models used across REST endpoints and WebSocket messages.
Keeping them in one place avoids circular imports.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class CANFrame(BaseModel):
    """A single CAN frame as it travels over the WebSocket."""
    id: str                        # Hex string e.g. "0x1FF"
    dlc: int                       # Data length code (0-8 or up to 15 for FD / byte length)
    data: list[int]                # Raw bytes as ints (up to 64 bytes for CAN FD)
    timestamp: float               # Seconds since bus open
    is_extended_id: bool = False
    is_fd: bool = False            # True for CAN FD frames
    bitrate_switch: bool = False   # True if Bit Rate Switch (BRS) is set
    error_state_indicator: bool = False
    channel: int = 0
    signals: dict[str, float] = {}  # Populated if a DBC is loaded


class ConnectionStatus(BaseModel):
    connected: bool
    interface: str
    channel: str
    bitrate: int
    index: int
    fd: bool = False
    data_bitrate: int = 2_000_000
    error: str | None = None


class ConnectRequest(BaseModel):
    interface: str = "gs_usb"
    # channel is a string for slcan/socketcan (e.g. "COM3", "can0")
    # and an int for gs_usb (device index).
    # Accept both; bus.py passes it as `index` for gs_usb.
    channel: str | int = ""
    bitrate: int = 500_000
    baudrate: int = 115_200
    index: int = 0
    fd: bool = False
    data_bitrate: int = 2_000_000


class SendFrameRequest(BaseModel):
    id: int                        # Arbitration ID as integer
    dlc: int = 8                   # Frame DLC
    data: list[int]                # Up to 8 bytes (Classic CAN) or up to 64 bytes (CAN FD)
    is_extended_id: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False

    @field_validator("data")
    @classmethod
    def validate_data(cls, v: list[int]) -> list[int]:
        if len(v) > 64:
            raise ValueError("CAN frame payload cannot exceed 64 bytes")
        for b in v:
            if not 0 <= b <= 255:
                raise ValueError(f"Byte value out of range [0, 255]: {b}")
        return v


class DBCInfo(BaseModel):
    message_count: int
    messages: list[dict]           # name, id, signals[]