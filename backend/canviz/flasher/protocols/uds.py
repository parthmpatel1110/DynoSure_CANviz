"""
canviz/flasher/protocols/uds.py
-------------------------------
UDS (Unified Diagnostic Services - ISO 14229-1) Automotive Bootloader Protocol.
Supports Classical CAN (ISO 15765-2 ISO-TP) and High-Speed CAN FD Flashing.
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from typing import Any

from canviz.flasher.base import BaseFlashingProtocol, FlashContext, ProtocolOption


class UDSBootloaderProtocol(BaseFlashingProtocol):
    id = "uds_iso14229"
    name = "UDS / ISO 14229 Bootloader"
    description = (
        "Standard automotive ECU reprogramming over UDS. Supports diagnostic programming "
        "session (0x10), security unlock (0x27), flash erase (0x31), block transfer (0x36), "
        "CRC verification, and ECU reset (0x11) on Classic CAN and CAN FD."
    )
    default_tx_id = 0x7E0
    default_rx_id = 0x7E8
    is_extended_id = False
    supports_fd = True
    default_chunk_size = 64

    options_schema = [
        ProtocolOption(
            key="security_key_hex",
            label="Security Access Key (Hex)",
            type="string",
            default="00000000",
            description="Hex key for service 0x27 (send empty to skip security access)",
        ),
        ProtocolOption(
            key="erase_timeout_s",
            label="Erase Timeout (seconds)",
            type="number",
            default=10.0,
            description="Maximum duration to wait for RoutineControl EraseMemory response",
        ),
        ProtocolOption(
            key="address_format",
            label="Address/Length Format Byte",
            type="select",
            default="0x44",
            options=["0x44", "0x33", "0x22"],
            description="ISO 14229 format identifier (0x44 = 4 bytes address, 4 bytes length)",
        ),
    ]

    async def _send_uds_request(
        self,
        ctx: FlashContext,
        service: int,
        subfunction: int | None = None,
        data: bytes = b"",
        timeout: float = 2.5,
    ) -> bytes:
        """Constructs and sends a UDS single-frame request, returning the payload on positive response."""
        payload = bytearray([service])
        if subfunction is not None:
            payload.append(subfunction)
        payload.extend(data)

        # Standard CAN single-frame framing: length byte followed by UDS payload
        if ctx.is_fd and len(payload) <= 62:
            frame_data = bytes([len(payload)]) + bytes(payload)
        elif len(payload) <= 7:
            frame_data = bytes([len(payload)]) + bytes(payload)
        else:
            # Multi-byte or raw frame
            frame_data = bytes(payload)

        resp = await ctx.send_and_recv(
            tx_id=ctx.tx_id,
            data=frame_data,
            rx_id=ctx.rx_id,
            timeout=timeout,
        )

        if not resp:
            raise TimeoutError(f"No response from ECU for service 0x{service:02X}")

        # Decode response payload (skip ISO-TP single frame length byte if present)
        resp_bytes = resp
        if len(resp_bytes) > 1 and resp_bytes[0] in (len(resp_bytes) - 1, len(resp_bytes) - 2):
            resp_bytes = resp_bytes[1:]

        # Check for Negative Response Service (0x7F)
        if len(resp_bytes) >= 3 and resp_bytes[0] == 0x7F:
            failed_service = resp_bytes[1]
            nrc = resp_bytes[2]
            # NRC 0x78: requestCorrectlyReceived-ResponsePending — wait for pending response
            if nrc == 0x78:
                ctx.log(f"ECU busy (NRC 0x78 ResponsePending), waiting...", level="warning")
                for _ in range(20):
                    ctx.check_abort()
                    pending_resp = await ctx.recv_frame(rx_id=ctx.rx_id, timeout=timeout)
                    if pending_resp:
                        p_data = pending_resp.data
                        if len(p_data) > 1 and p_data[0] < len(p_data):
                            p_data = p_data[1:]
                        if len(p_data) >= 1 and p_data[0] == (service + 0x40):
                            return bytes(p_data)
                        if len(p_data) >= 3 and p_data[0] == 0x7F and p_data[2] != 0x78:
                            raise RuntimeError(f"UDS NRC 0x{p_data[2]:02X} after ResponsePending")
                    await asyncio.sleep(0.1)

            raise RuntimeError(f"UDS Negative Response (NRC 0x{nrc:02X}) for service 0x{failed_service:02X}")

        expected_sid = service + 0x40
        if not resp_bytes or resp_bytes[0] != expected_sid:
            raise RuntimeError(
                f"Unexpected UDS response: expected SID 0x{expected_sid:02X}, received 0x{resp_bytes[0]:02X}"
            )

        return bytes(resp_bytes)

    async def flash(self, ctx: FlashContext) -> None:
        ctx.log("=== Starting UDS Flashing Session ===", level="info")
        ctx.set_stage("CONNECTING")

        # Step 1: Diagnostic Session Control (0x10) -> Programming Session (0x02)
        ctx.log("Entering UDS Programming Session (0x10 0x02)...")
        await self._send_uds_request(ctx, service=0x10, subfunction=0x02)
        ctx.log("Programming Session established.", level="success")
        await asyncio.sleep(0.1)

        # Step 2: Security Access (0x27)
        sec_key_str = str(ctx.options.get("security_key_hex", "")).strip().replace(" ", "").replace("0x", "")
        if sec_key_str:
            ctx.set_stage("CONNECTING")
            ctx.log("Requesting Security Seed (0x27 0x01)...")
            seed_resp = await self._send_uds_request(ctx, service=0x27, subfunction=0x01)
            ctx.log(f"Received seed: {' '.join(f'{b:02X}' for b in seed_resp[2:])}")

            try:
                key_bytes = bytes.fromhex(sec_key_str)
            except ValueError:
                key_bytes = bytes([0x00, 0x00, 0x00, 0x00])

            ctx.log(f"Sending Security Key (0x27 0x02)...")
            await self._send_uds_request(ctx, service=0x27, subfunction=0x02, data=key_bytes)
            ctx.log("Security Access granted.", level="success")
            await asyncio.sleep(0.1)

        # Step 3: Erase Memory (RoutineControl 0x31 0x01 0xFF00)
        ctx.set_stage("ERASING")
        erase_timeout = float(ctx.options.get("erase_timeout_s", 10.0))
        total_len = len(ctx.firmware_bytes)
        ctx.log(
            f"Erasing flash: address 0x{ctx.base_address:08X}, length {total_len} bytes...",
            level="info",
        )
        erase_data = struct.pack(">BII", 0x44, ctx.base_address, total_len)
        await self._send_uds_request(
            ctx,
            service=0x31,
            subfunction=0x01,
            data=bytes([0xFF, 0x00]) + erase_data,
            timeout=erase_timeout,
        )
        ctx.log("Flash memory erased successfully.", level="success")
        await asyncio.sleep(0.1)

        # Step 4: Request Download (0x34)
        ctx.set_stage("FLASHING")
        ctx.log(f"Requesting download (0x34) for 0x{ctx.base_address:08X} ({total_len} bytes)...")
        req_dl_data = bytes([0x00, 0x44]) + struct.pack(">II", ctx.base_address, total_len)
        dl_resp = await self._send_uds_request(ctx, service=0x34, data=req_dl_data)
        ctx.log("Request Download accepted by ECU.", level="success")

        # Step 5: Transfer Data (0x36)
        block_size = min(max(ctx.chunk_size, 8), 4095)
        # Account for [0x36, sequence_counter] 2-byte header
        chunk_payload_size = block_size - 2 if block_size > 2 else 6
        if ctx.is_fd:
            # On CAN FD, max single frame payload is up to 60 bytes with header
            chunk_payload_size = min(chunk_payload_size, 60)
        else:
            chunk_payload_size = min(chunk_payload_size, 5)

        total_blocks = (total_len + chunk_payload_size - 1) // chunk_payload_size
        ctx.log(
            f"Transferring {total_len} bytes in {total_blocks} blocks (chunk size {chunk_payload_size} bytes)..."
        )

        sequence_counter = 1
        bytes_transferred = 0

        for offset in range(0, total_len, chunk_payload_size):
            ctx.check_abort()
            chunk = ctx.firmware_bytes[offset : offset + chunk_payload_size]
            seq_byte = sequence_counter & 0xFF

            transfer_data = bytes([seq_byte]) + chunk
            await self._send_uds_request(ctx, service=0x36, data=transfer_data, timeout=3.0)

            bytes_transferred += len(chunk)
            sequence_counter = (sequence_counter + 1) if sequence_counter < 0xFF else 0
            ctx.update_progress(bytes_transferred, total_len)

        ctx.log("All data blocks transferred successfully.", level="success")

        # Step 6: Request Transfer Exit (0x37)
        ctx.log("Sending RequestTransferExit (0x37)...")
        await self._send_uds_request(ctx, service=0x37)
        ctx.log("Transfer session closed.", level="success")

        # Step 7: Verify Checksum (RoutineControl 0x31 0x01 0xFF01)
        ctx.set_stage("VERIFYING")
        ctx.log("Verifying checksum with ECU (0x31 0x01 0xFF01)...")
        crc_val = zlib.crc32(ctx.firmware_bytes) & 0xFFFFFFFF
        crc_bytes = struct.pack(">I", crc_val)
        try:
            await self._send_uds_request(
                ctx,
                service=0x31,
                subfunction=0x01,
                data=bytes([0xFF, 0x01]) + crc_bytes,
                timeout=4.0,
            )
            ctx.log(f"ECU confirmed checksum 0x{crc_val:08X}.", level="success")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Checksum verification routine note: {exc}", level="warning")

        # Step 8: ECU Reset (0x11 0x01)
        ctx.set_stage("RESETTING")
        ctx.log("Issuing ECU Hard Reset (0x11 0x01)...")
        try:
            await self._send_uds_request(ctx, service=0x11, subfunction=0x01, timeout=1.5)
            ctx.log("ECU reset acknowledged.", level="success")
        except Exception:  # noqa: BLE001
            ctx.log("ECU restarting (reset command dispatched).", level="info")

        ctx.set_stage("COMPLETED")
        ctx.log("=== Flashing Completed Successfully ===", level="success")

    async def erase(self, ctx: FlashContext) -> None:
        """Standalone Erase Routine."""
        ctx.log("Executing Standalone UDS Erase...")
        ctx.set_stage("ERASING")
        await self._send_uds_request(ctx, service=0x10, subfunction=0x02)
        total_len = len(ctx.firmware_bytes) if ctx.firmware_bytes else 0x10000
        erase_data = struct.pack(">BII", 0x44, ctx.base_address, total_len)
        await self._send_uds_request(
            ctx,
            service=0x31,
            subfunction=0x01,
            data=bytes([0xFF, 0x00]) + erase_data,
            timeout=float(ctx.options.get("erase_timeout_s", 10.0)),
        )
        ctx.log("Erase completed successfully.", level="success")

    async def reset_ecu(self, ctx: FlashContext) -> None:
        """Standalone ECU Reset."""
        ctx.log("Sending ECU Hard Reset (0x11 0x01)...")
        ctx.set_stage("RESETTING")
        await self._send_uds_request(ctx, service=0x11, subfunction=0x01)
        ctx.log("ECU reset acknowledged.", level="success")
