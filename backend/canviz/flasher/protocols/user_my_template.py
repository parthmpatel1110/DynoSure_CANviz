"""
canviz/flasher/protocols/template.py
------------------------------------
Custom Protocol Boilerplate Template.

To write your own custom CAN flashing protocol:
1. Subclass BaseFlashingProtocol.
2. Define `id`, `name`, `description`, default IDs, and `options_schema`.
3. Implement `async def flash(self, ctx: FlashContext) -> None`.
4. Drop this file into `canviz/flasher/protocols/` or upload it through the web UI!
"""

from __future__ import annotations

import asyncio
from canviz.flasher.base import BaseFlashingProtocol, FlashContext, ProtocolOption


class CustomUserProtocol(BaseFlashingProtocol):
    # Unique protocol identifier (alphanumeric + underscores)
    id = "custom_user_protocol"

    # Human-readable title displayed in the UI selector
    name = "Custom User Protocol (Template)"

    # Short description of target hardware and protocol behaviour
    description = "User-defined custom bootloader protocol template."

    # Default CAN identifiers
    default_tx_id = 0x550
    default_rx_id = 0x551
    is_extended_id = False
    supports_fd = True
    default_chunk_size = 64

    # Configurable parameters rendered dynamically in the UI
    options_schema = [
        ProtocolOption(
            key="unlock_pin",
            label="Device Unlock PIN",
            type="string",
            default="1234",
            description="PIN required to enter bootloader mode",
        ),
        ProtocolOption(
            key="verify_flash",
            label="Verify Flash After Write",
            type="boolean",
            default=True,
            description="Whether to perform checksum verification after writing",
        ),
    ]

    async def flash(self, ctx: FlashContext) -> None:
        """
        Main flashing routine executed asynchronously.
        ctx provides:
          ctx.bus               -> active python-can bus object
          ctx.tx_id / ctx.rx_id -> target CAN arbitration IDs
          ctx.firmware_bytes    -> raw bytes to flash
          ctx.base_address      -> target starting address
          ctx.options           -> dictionary of options configured in the UI
          ctx.send_frame(...)   -> transmit CAN / CAN FD frame
          ctx.recv_frame(...)   -> wait for CAN frame with timeout
          ctx.send_and_recv(...) -> send and await response
          ctx.log(...)          -> emit colored log to diagnostic terminal
          ctx.set_stage(...)    -> update stage (CONNECTING, ERASING, FLASHING...)
          ctx.update_progress(done, total) -> update progress bar & speed metrics
          ctx.check_abort()     -> checks if user clicked 'Abort'
        """
        ctx.log(f"Starting {self.name}...", level="info")

        # Stage 1: Connect & Handshake
        ctx.set_stage("CONNECTING")
        ctx.log("Pinging target controller...")
        resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=[0x01, 0x00],
            rx_id=ctx.rx_id,
            timeout=2.0,
        )
        if not resp:
            raise TimeoutError("Device did not respond to ping.")
        ctx.log("Controller connected.", level="success")

        # Stage 2: Erase Memory
        ctx.set_stage("ERASING")
        ctx.log("Erasing target memory sectors...")
        await asyncio.sleep(0.5)  # Replace with device erase command
        ctx.log("Erase completed.", level="success")

        # Stage 3: Flashing Blocks
        ctx.set_stage("FLASHING")
        total = len(ctx.firmware_bytes)
        chunk_size = ctx.chunk_size

        for offset in range(0, total, chunk_size):
            # Always call check_abort() inside write loops to handle user cancellation
            ctx.check_abort()

            chunk = ctx.firmware_bytes[offset : offset + chunk_size]
            current_addr = ctx.base_address + offset

            # Example: Send block over CAN
            # await ctx.send_frame(arb_id=ctx.tx_id, data=chunk)

            # Update progress bar
            ctx.update_progress(offset + len(chunk), total)

        ctx.log("All blocks successfully flashed.", level="success")

        # Stage 4: Reset / Jump to Application
        ctx.set_stage("RESETTING")
        ctx.log("Restarting controller into application...")
        # await ctx.send_frame(arb_id=ctx.tx_id, data=[0x05, 0x01])

        ctx.set_stage("COMPLETED")
        ctx.log("Flashing completed successfully!", level="success")

    async def erase(self, ctx: FlashContext) -> None:
        """Called when user clicks 'Erase Only' in UI."""
        ctx.set_stage("ERASING")
        ctx.log("Executing custom erase...", level="info")

    async def verify(self, ctx: FlashContext) -> None:
        """Called when user clicks 'Verify Only' in UI."""
        ctx.set_stage("VERIFYING")
        ctx.log("Executing custom verify...", level="info")

    async def reset_ecu(self, ctx: FlashContext) -> None:
        """Called when user clicks 'Reset ECU' in UI."""
        ctx.set_stage("RESETTING")
        ctx.log("Executing custom reset...", level="info")
