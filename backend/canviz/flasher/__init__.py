"""
canviz/flasher
--------------
Universal CAN Flashing tool with programmatically extensible protocols.
"""

from canviz.flasher.base import (
    BaseFlashingProtocol,
    FlashContext,
    FlasherAbortedException,
    ProtocolOption,
)
from canviz.flasher.firmware_parser import FirmwareInfo, parse_firmware
from canviz.flasher.manager import FlasherManager, flasher_manager

# Auto-discover and register built-in and user protocols
flasher_manager.discover_protocols()

__all__ = [
    "BaseFlashingProtocol",
    "FirmwareInfo",
    "FlashContext",
    "FlasherAbortedException",
    "FlasherManager",
    "ProtocolOption",
    "flasher_manager",
    "parse_firmware",
]
