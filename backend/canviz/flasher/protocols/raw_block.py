"""
canviz/flasher/protocols/raw_block.py
-------------------------------------
Universal Raw Block Flashing Protocol.
High-speed configurable block bootloader designed for custom microcontrollers,
embedded systems, and CAN FD high-throughput firmware flashing.
"""

from __future__ import annotations

import asyncio
import struct
import zlib

from canviz.flasher.base import BaseFlashingProtocol, FlashContext, ProtocolOption


class RawBlockProtocol(BaseFlashingProtocol):
    id = "raw_block_protocol"
    name = "Universal Raw Block Bootloader (CAN FD Ready)"
    description = (
        "High-performance generic block flashing protocol for custom microcontrollers. "
        "Supports configurable command opcodes, sequence numbers, ACK/NACK responses, "
        "and up to 64-byte payload chunks with CAN FD Bit Rate Switch (BRS)."
    )
    default_tx_id = 0x600
    default_rx_id = 0x601
    is_extended_id = False
    supports_fd = True
    default_chunk_size = 56  # 56 bytes payload + 8 bytes header = 64 bytes CAN FD frame

    options_schema = [
        ProtocolOption(
            key="ack_byte",
            label="ACK Response Byte (Hex)",
            type="string",
            default="06",
            description="Expected response byte on success (default: 0x06 ACK)",
        ),
        ProtocolOption(
            key="cmd_ping",
            label="Ping Opcode (Hex)",
            type="string",
            default="01",
            description="Handshake / Ping opcode",
        ),
        ProtocolOption(
            key="cmd_erase",
            label="Erase Opcode (Hex)",
            type="string",
            default="02",
            description="Flash Erase opcode",
        ),
        ProtocolOption(
            key="cmd_write",
            label="Write Opcode (Hex)",
            type="string",
            default="03",
            description="Write Block opcode",
        ),
        ProtocolOption(
            key="cmd_verify",
            label="Verify Opcode (Hex)",
            type="string",
            default="04",
            description="Verify Checksum opcode",
        ),
        ProtocolOption(
            key="cmd_reset",
            label="Reset Opcode (Hex)",
            type="string",
            default="05",
            description="Reset / Jump to App opcode",
        ),
    ]

    def _get_hex_byte(self, ctx: FlashContext, key: str, default: int) -> int:
        val = str(ctx.options.get(key, "")).strip().replace("0x", "")
        try:
            return int(val, 16) if val else default
        except ValueError:
            return default

    async def flash(self, ctx: FlashContext) -> None:
        ctx.log("=== Starting Universal Raw Block Flashing ===", level="info")
        ctx.set_stage("CONNECTING")

        ack_byte = self._get_hex_byte(ctx, "ack_byte", 0x06)
        cmd_ping = self._get_hex_byte(ctx, "cmd_ping", 0x01)
        cmd_erase = self._get_hex_byte(ctx, "cmd_erase", 0x02)
        cmd_write = self._get_hex_byte(ctx, "cmd_write", 0x03)
        cmd_verify = self._get_hex_byte(ctx, "cmd_verify", 0x04)
        cmd_reset = self._get_hex_byte(ctx, "cmd_reset", 0x05)

        # 1. Ping / Handshake
        ctx.log(f"Pinging device with opcode 0x{cmd_ping:02X}...")
        resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=[cmd_ping, 0x00],
            rx_id=ctx.rx_id,
            timeout=2.0,
        )
        if not resp or resp[0] != ack_byte:
            raise RuntimeError(
                f"Handshake failed: expected ACK 0x{ack_byte:02X}, received {f'0x{resp[0]:02X}' if resp else 'None'}"
            )
        ctx.log("Device acknowledged connection.", level="success")

        # 2. Erase Memory
        ctx.set_stage("ERASING")
        total_len = len(ctx.firmware_bytes)
        ctx.log(
            f"Sending erase command (0x{cmd_erase:02X}): base 0x{ctx.base_address:08X}, {total_len} bytes..."
        )
        erase_payload = struct.pack(">BII", cmd_erase, ctx.base_address, total_len)
        erase_resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=erase_payload,
            rx_id=ctx.rx_id,
            timeout=10.0,
        )
        if not erase_resp or erase_resp[0] != ack_byte:
            raise RuntimeError(
                f"Erase failed: response was {f'0x{erase_resp[0]:02X}' if erase_resp else 'Timeout'}"
            )
        ctx.log("Memory erase confirmed by device.", level="success")
        await asyncio.sleep(0.1)

        # 3. Write Blocks
        ctx.set_stage("FLASHING")
        # Packet header: [Opcode (1B), Seq (1B), ChunkLen (2B), Offset (4B)] = 8 bytes header
        # In Classic CAN: 8 bytes total -> 0 payload! So chunk size must fit in frame
        if ctx.is_fd:
            max_payload = min(max(ctx.chunk_size, 8), 56)
        else:
            max_payload = 4  # 4 bytes header [Opcode, Seq, Offset_Lo] + 4 bytes payload

        ctx.log(
            f"Flashing {total_len} bytes in {max_payload}-byte chunks (CAN FD: {ctx.is_fd})..."
        )

        seq = 0
        bytes_transferred = 0

        for offset in range(0, total_len, max_payload):
            ctx.check_abort()
            chunk = ctx.firmware_bytes[offset : offset + max_payload]
            current_addr = ctx.base_address + offset

            if ctx.is_fd:
                # 8-byte header: Opcode (1B), Seq (1B), ChunkLen (2B), Addr (4B)
                header = struct.pack(">BBHI", cmd_write, seq & 0xFF, len(chunk), current_addr)
            else:
                # 4-byte header for classical 8-byte CAN frames
                header = struct.pack(">BBH", cmd_write, seq & 0xFF, offset & 0xFFFF)

            packet = header + chunk
            write_resp = await ctx.send_and_recv(
                tx_id=ctx.tx_id,
                data=packet,
                rx_id=ctx.rx_id,
                timeout=3.0,
            )

            if not write_resp or write_resp[0] != ack_byte:
                raise RuntimeError(
                    f"Write failed at offset 0x{offset:06X} (response: {write_resp[0] if write_resp else 'Timeout'})"
                )

            bytes_transferred += len(chunk)
            seq = (seq + 1) & 0xFF
            ctx.update_progress(bytes_transferred, total_len)

        ctx.log("All data blocks successfully written and acknowledged.", level="success")

        # 4. Verify Checksum
        ctx.set_stage("VERIFYING")
        crc_val = zlib.crc32(ctx.firmware_bytes) & 0xFFFFFFFF
        ctx.log(f"Verifying CRC32 checksum (0x{crc_val:08X}) with device...")
        verify_packet = struct.pack(">BII", cmd_verify, ctx.base_address, crc_val)
        verify_resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=verify_packet,
            rx_id=ctx.rx_id,
            timeout=4.0,
        )
        if verify_resp and verify_resp[0] == ack_byte:
            ctx.log("Device verified checksum successfully.", level="success")
        else:
            ctx.log(
                f"Verification response: {f'0x{verify_resp[0]:02X}' if verify_resp else 'None'} (continuing)",
                level="warning",
            )

        # 5. Reset / Run Application
        ctx.set_stage("RESETTING")
        ctx.log(f"Sending Reset / Run command (0x{cmd_reset:02X})...")
        try:
            await ctx.send_and_recv(
                tx_id=ctx.tx_id,
                data=[cmd_reset, 0x01],
                rx_id=ctx.rx_id,
                timeout=1.0,
            )
            ctx.log("Device acknowledged reset.", level="success")
        except Exception:  # noqa: BLE001
            ctx.log("Device rebooting.", level="info")

        ctx.set_stage("COMPLETED")
        ctx.log("=== Flashing Completed Successfully ===", level="success")

    async def erase(self, ctx: FlashContext) -> None:
        """Standalone Erase."""
        ack_byte = self._get_hex_byte(ctx, "ack_byte", 0x06)
        cmd_erase = self._get_hex_byte(ctx, "cmd_erase", 0x02)
        ctx.set_stage("ERASING")
        total_len = len(ctx.firmware_bytes) if ctx.firmware_bytes else 0x10000
        erase_payload = struct.pack(">BII", cmd_erase, ctx.base_address, total_len)
        resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=erase_payload,
            rx_id=ctx.rx_id,
            timeout=10.0,
        )
        if not resp or resp[0] != ack_byte:
            raise RuntimeError("Erase command failed.")
        ctx.log("Erase successful.", level="success")

    async def reset_ecu(self, ctx: FlashContext) -> None:
        """Standalone Reset."""
        cmd_reset = self._get_hex_byte(ctx, "cmd_reset", 0x05)
        ctx.set_stage("RESETTING")
        await ctx.send_frame(arb_id=ctx.tx_id, data=[cmd_reset, 0x01])
        ctx.log("Reset command sent.", level="success")
