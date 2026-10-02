"""
canviz/flasher/protocols/stm32.py
---------------------------------
STM32 System Memory CAN Bootloader Protocol (ST AN3154).
Compatible with built-in ROM bootloaders in STM32F0, STM32F1, STM32F2, STM32F3,
STM32F4, STM32F7, STM32G0, STM32G4, STM32L0, STM32L4, etc.
"""

from __future__ import annotations

import asyncio
import struct

from canviz.flasher.base import BaseFlashingProtocol, FlashContext, ProtocolOption

STM32_ACK = 0x79
STM32_NACK = 0x1F


def _calc_xor_checksum(data: bytes | list[int]) -> int:
    """Calculate STM32 XOR checksum."""
    cs = 0
    for b in data:
        cs ^= b
    return cs


class STM32BootloaderProtocol(BaseFlashingProtocol):
    id = "stm32_can_bootloader"
    name = "STM32 CAN Bootloader (AN3154)"
    description = (
        "STMicroelectronics in-system programming (AN3154) for STM32 microcontrollers. "
        "Supports Get Command (0x00), Extended Erase (0x44), Write Memory (0x31) up to "
        "256-byte blocks with XOR checksum, and Go Command (0x21)."
    )
    default_tx_id = 0x079
    default_rx_id = 0x078
    is_extended_id = False
    supports_fd = False
    default_chunk_size = 128

    options_schema = [
        ProtocolOption(
            key="mass_erase",
            label="Perform Global Mass Erase",
            type="boolean",
            default=True,
            description="Perform full flash memory erase before writing (recommended)",
        ),
        ProtocolOption(
            key="erase_timeout_s",
            label="Erase Timeout (seconds)",
            type="number",
            default=15.0,
            description="Timeout for mass flash memory erase",
        ),
        ProtocolOption(
            key="jump_after_flash",
            label="Execute Application (Go Command)",
            type="boolean",
            default=True,
            description="Jump to firmware base address after successful flashing",
        ),
    ]

    async def _send_command(self, ctx: FlashContext, cmd: int, timeout: float = 2.0) -> None:
        """Send command byte and inverted complement byte, expecting STM32_ACK."""
        cmd_complement = (~cmd) & 0xFF
        resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=[cmd, cmd_complement],
            rx_id=ctx.rx_id,
            timeout=timeout,
        )
        if not resp:
            raise TimeoutError(f"No response from STM32 bootloader for command 0x{cmd:02X}")
        if resp[0] != STM32_ACK:
            raise RuntimeError(
                f"STM32 Bootloader rejected command 0x{cmd:02X} (response: 0x{resp[0]:02X})"
            )

    async def _send_address(self, ctx: FlashContext, address: int, timeout: float = 2.0) -> None:
        """Send 4-byte big-endian address with XOR checksum, expecting STM32_ACK."""
        addr_bytes = struct.pack(">I", address)
        cs = _calc_xor_checksum(addr_bytes)
        resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=addr_bytes + bytes([cs]),
            rx_id=ctx.rx_id,
            timeout=timeout,
        )
        if not resp or resp[0] != STM32_ACK:
            raise RuntimeError(f"STM32 Bootloader rejected address 0x{address:08X}")

    async def flash(self, ctx: FlashContext) -> None:
        ctx.log("=== Starting STM32 Bootloader Flashing ===", level="info")
        ctx.set_stage("CONNECTING")

        # Step 1: Handshake / Ping with Get Command (0x00)
        ctx.log("Pinging STM32 Bootloader with Get Command (0x00)...")
        await self._send_command(ctx, cmd=0x00, timeout=2.0)
        # Drain the returned version/command payload
        ver_resp = await ctx.recv_frame(rx_id=ctx.rx_id, timeout=1.0)
        if ver_resp and len(ver_resp.data) > 0:
            ctx.log(f"STM32 Bootloader version: 0x{ver_resp.data[0]:02X}", level="success")

        # Step 2: Flash Erase (Extended Erase 0x44 or Erase 0x43)
        if ctx.options.get("mass_erase", True):
            ctx.set_stage("ERASING")
            ctx.log("Executing STM32 Global Mass Erase (0x44)...")
            erase_timeout = float(ctx.options.get("erase_timeout_s", 15.0))
            await self._send_command(ctx, cmd=0x44, timeout=2.0)

            # Special mass erase operand: 0xFFFF with XOR checksum 0x00
            mass_erase_data = bytes([0xFF, 0xFF, 0x00])
            erase_resp = await ctx.send_and_recv(
                tx_id=ctx.tx_id,
                data=mass_erase_data,
                rx_id=ctx.rx_id,
                timeout=erase_timeout,
            )
            if not erase_resp or erase_resp[0] != STM32_ACK:
                raise RuntimeError("STM32 Mass Erase failed or rejected.")
            ctx.log("Flash memory mass erased successfully.", level="success")
            await asyncio.sleep(0.2)

        # Step 3: Write Memory (0x31)
        ctx.set_stage("FLASHING")
        total_len = len(ctx.firmware_bytes)
        chunk_size = min(max(ctx.chunk_size, 16), 256)
        # STM32 protocol requires chunks to be a multiple of 4 bytes
        chunk_size = (chunk_size // 4) * 4

        ctx.log(
            f"Writing {total_len} bytes to 0x{ctx.base_address:08X} in {chunk_size}-byte blocks..."
        )

        bytes_transferred = 0
        for offset in range(0, total_len, chunk_size):
            ctx.check_abort()
            chunk = ctx.firmware_bytes[offset : offset + chunk_size]
            current_addr = ctx.base_address + offset

            # 1. Send Write Memory command (0x31)
            await self._send_command(ctx, cmd=0x31, timeout=2.0)

            # 2. Send 32-bit address + checksum
            await self._send_address(ctx, current_addr, timeout=2.0)

            # 3. Send N-1 count byte + data payload + XOR checksum
            # For STM32: count byte is N - 1 (e.g. 127 for 128 bytes)
            n_minus_one = len(chunk) - 1
            payload_with_count = bytes([n_minus_one]) + chunk
            cs = _calc_xor_checksum(payload_with_count)

            # Send payload (can be chunked over CAN frames)
            resp = await ctx.send_and_recv(
                tx_id=ctx.tx_id,
                data=payload_with_count + bytes([cs]),
                rx_id=ctx.rx_id,
                timeout=3.0,
            )
            if not resp or resp[0] != STM32_ACK:
                raise RuntimeError(
                    f"STM32 write failed at address 0x{current_addr:08X} (resp: {resp[0] if resp else 'None'})"
                )

            bytes_transferred += len(chunk)
            ctx.update_progress(bytes_transferred, total_len)

        ctx.log("Firmware writing complete.", level="success")

        # Step 4: Go Command (0x21) to start application
        if ctx.options.get("jump_after_flash", True):
            ctx.set_stage("RESETTING")
            ctx.log(f"Jumping to application address 0x{ctx.base_address:08X} (Go Command 0x21)...")
            try:
                await self._send_command(ctx, cmd=0x21, timeout=2.0)
                await self._send_address(ctx, ctx.base_address, timeout=2.0)
                ctx.log("Application started successfully.", level="success")
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"Go command dispatched: {exc}", level="info")

        ctx.set_stage("COMPLETED")
        ctx.log("=== STM32 Flashing Completed Successfully ===", level="success")

    async def erase(self, ctx: FlashContext) -> None:
        """Standalone STM32 Mass Erase."""
        ctx.log("Executing Standalone STM32 Mass Erase...")
        ctx.set_stage("ERASING")
        erase_timeout = float(ctx.options.get("erase_timeout_s", 15.0))
        await self._send_command(ctx, cmd=0x44, timeout=2.0)
        mass_erase_data = bytes([0xFF, 0xFF, 0x00])
        erase_resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=mass_erase_data,
            rx_id=ctx.rx_id,
            timeout=erase_timeout,
        )
        if not erase_resp or erase_resp[0] != STM32_ACK:
            raise RuntimeError("STM32 Mass Erase failed.")
        ctx.log("Mass Erase successful.", level="success")

    async def reset_ecu(self, ctx: FlashContext) -> None:
        """Jump to application entry point."""
        ctx.log(f"Executing Go Command to 0x{ctx.base_address:08X}...")
        ctx.set_stage("RESETTING")
        await self._send_command(ctx, cmd=0x21, timeout=2.0)
        await self._send_address(ctx, ctx.base_address, timeout=2.0)
        ctx.log("Jump to application complete.", level="success")
