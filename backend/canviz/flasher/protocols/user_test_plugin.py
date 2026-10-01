
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
