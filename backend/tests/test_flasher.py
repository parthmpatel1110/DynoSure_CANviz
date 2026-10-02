from pathlib import Path

import pytest

import canviz.flasher.protocols.template as tmpl
from canviz.flasher.base import (
    FlashContext,
    FlasherAbortedException,
)
from canviz.flasher.manager import FlasherManager


def test_flasher_manager_builtins():
    mgr = FlasherManager()
    mgr.discover_protocols()
    protos = mgr.list_protocols()

    proto_ids = [p["id"] for p in protos]
    assert "raw_block_protocol" in proto_ids
    assert "uds_iso14229" in proto_ids
    assert "stm32_can_bootloader" in proto_ids

    raw = mgr.get_protocol("raw_block_protocol")
    assert raw is not None
    assert raw.supports_fd is True
    assert raw.default_chunk_size == 56

def test_flasher_template_and_ast_validation():
    mgr = FlasherManager()
    template = Path(tmpl.__file__).read_text(encoding="utf-8")
    assert "BaseFlashingProtocol" in template
    assert "class CustomUserProtocol" in template

    # Valid template compilation & loading
    res = mgr.load_protocol_from_code(template, filename="my_template.py")
    assert "custom_user_protocol" in res["registered"]

    # Invalid code syntax
    bad_code = "def broken(: print('syntax error')"
    with pytest.raises(ValueError):
        mgr.load_protocol_from_code(bad_code, filename="bad.py")

def test_custom_protocol_registration():
    mgr = FlasherManager()

    custom_code = '''
from canviz.flasher.base import BaseFlashingProtocol, FlashContext

class UnitTestingProtocol(BaseFlashingProtocol):
    id = "unit_test_proto"
    name = "Unit Test Protocol"
    version = "1.0.0"
    author = "Pytest"
    description = "Test dynamic loading"
    supports_fd = True
    default_tx_id = 0x100
    default_rx_id = 0x101

    async def flash(self, ctx: FlashContext) -> None:
        ctx.set_stage("TESTING")
        ctx.update_progress(100, 100)
'''
    res = mgr.load_protocol_from_code(custom_code, filename="test_plugin.py")
    assert "unit_test_proto" in res["registered"]

    proto = mgr.get_protocol("unit_test_proto")
    assert proto is not None
    assert proto.name == "Unit Test Protocol"
    assert proto.supports_fd is True

@pytest.mark.asyncio
async def test_flash_context_abort_check():
    aborted = False

    def is_aborted():
        return aborted

    logs = []
    stages = []
    progress = []

    class MockBus:
        pass

    ctx = FlashContext(
        bus=MockBus(),
        tx_id=0x7E0,
        rx_id=0x7E8,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
        base_address=0x08000000,
        chunk_size=64,
        firmware_bytes=b"12345678",
        options={},
        log_callback=lambda msg, lvl: logs.append((lvl, msg)),
        stage_callback=lambda s: stages.append(s),
        progress_callback=lambda x, t: progress.append((x, t)),
        abort_check=is_aborted,
    )

    # Initially not aborted
    ctx.check_abort()

    # Logging and stage
    ctx.log("Hello from test", level="info")
    assert len(logs) == 1
    ctx.set_stage("INITIALIZING")
    assert stages == ["INITIALIZING"]

    # Mark abort
    aborted = True
    with pytest.raises(FlasherAbortedException):
        ctx.check_abort()
