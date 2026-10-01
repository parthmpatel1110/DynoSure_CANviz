"""
canviz/flasher/firmware_parser.py
---------------------------------
Multi-format firmware file parser supporting:
  - Raw Binary (.bin)
  - Intel HEX (.hex)
  - Motorola S-Record (.srec, .s19, .s28, .s37)

Extracts contiguous or gap-padded memory payload, determines base address,
and computes CRC32 and MD5 verification checksums.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FirmwareInfo:
    filename: str
    data: bytes
    base_address: int
    total_bytes: int
    crc32: str
    md5: str
    format: str

    def as_dict(self) -> dict:
        return {
            "filename": self.filename,
            "base_address": f"0x{self.base_address:08X}",
            "total_bytes": self.total_bytes,
            "crc32": self.crc32,
            "md5": self.md5,
            "format": self.format,
        }


def parse_intel_hex(text: str) -> tuple[int, bytes]:
    """
    Parse Intel HEX format records:
      :llaaaatt[dd...]cc
      ll = length
      aaaa = 16-bit address
      tt = record type:
        00 = Data
        01 = End of File
        02 = Extended Segment Address
        04 = Extended Linear Address
        05 = Start Linear Address
      dd = data bytes
      cc = checksum
    """
    segments: dict[int, int] = {}  # address -> byte
    base_ext = 0

    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith(":"):
            continue

        try:
            raw = bytes.fromhex(line[1:])
        except ValueError:
            continue

        if len(raw) < 5:
            continue

        # Checksum validation: sum of all bytes modulo 256 must be 0
        if sum(raw) % 256 != 0:
            raise ValueError(f"Intel HEX checksum mismatch in line: {line}")

        length = raw[0]
        offset = (raw[1] << 8) | raw[2]
        rectype = raw[3]
        data = raw[4 : 4 + length]

        if rectype == 0x00:  # Data record
            addr = base_ext + offset
            for i, b in enumerate(data):
                segments[addr + i] = b

        elif rectype == 0x01:  # End of file
            break

        elif rectype == 0x02:  # Extended segment address
            base_ext = ((data[0] << 8) | data[1]) << 4

        elif rectype == 0x04:  # Extended linear address
            base_ext = ((data[0] << 8) | data[1]) << 16

        elif rectype == 0x05:  # Start linear address
            pass

    if not segments:
        raise ValueError("No data records found in Intel HEX file.")

    min_addr = min(segments.keys())
    max_addr = max(segments.keys())
    total_len = max_addr - min_addr + 1

    # Fill unpopulated address gaps with 0xFF (standard flash erase value)
    out = bytearray([0xFF] * total_len)
    for addr, val in segments.items():
        out[addr - min_addr] = val

    return min_addr, bytes(out)


def parse_srec(text: str) -> tuple[int, bytes]:
    """
    Parse Motorola S-Record format:
      S<type><count><address><data><checksum>
      Types:
        S0: Header (ignored)
        S1: 16-bit address data
        S2: 24-bit address data
        S3: 32-bit address data
        S5/S6: Count
        S7/S8/S9: Termination
    """
    segments: dict[int, int] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("S"):
            continue

        stype = line[1]
        try:
            raw = bytes.fromhex(line[2:])
        except ValueError:
            continue

        if len(raw) < 3:
            continue

        # Checksum validation: ones' complement of sum of bytes
        if (sum(raw) & 0xFF) != 0xFF:
            raise ValueError(f"S-Record checksum mismatch in line: {line}")

        count = raw[0]
        payload = raw[1:-1]

        if stype == "1":  # 16-bit address
            addr = (payload[0] << 8) | payload[1]
            data = payload[2:]
        elif stype == "2":  # 24-bit address
            addr = (payload[0] << 16) | (payload[1] << 8) | payload[2]
            data = payload[3:]
        elif stype == "3":  # 32-bit address
            addr = (payload[0] << 24) | (payload[1] << 16) | (payload[2] << 8) | payload[3]
            data = payload[4:]
        else:
            continue

        for i, b in enumerate(data):
            segments[addr + i] = b

    if not segments:
        raise ValueError("No data records found in S-Record file.")

    min_addr = min(segments.keys())
    max_addr = max(segments.keys())
    total_len = max_addr - min_addr + 1

    out = bytearray([0xFF] * total_len)
    for addr, val in segments.items():
        out[addr - min_addr] = val

    return min_addr, bytes(out)


def parse_firmware(
    filename: str,
    content: bytes,
    default_base_address: int = 0x08000000,
) -> FirmwareInfo:
    """
    Auto-detect format and parse firmware into a unified FirmwareInfo structure.
    """
    suffix = Path(filename).suffix.lower()

    if suffix in (".hex", ".ihex"):
        text = content.decode("ascii", errors="replace")
        base_addr, data = parse_intel_hex(text)
        fmt = "Intel HEX"

    elif suffix in (".srec", ".s19", ".s28", ".s37", ".mot"):
        text = content.decode("ascii", errors="replace")
        base_addr, data = parse_srec(text)
        fmt = "Motorola S-Record"

    else:
        # Assume raw binary
        base_addr = default_base_address
        data = content
        fmt = "Raw Binary"

    crc = f"0x{zlib.crc32(data) & 0xFFFFFFFF:08X}"
    md5_hash = hashlib.md5(data).hexdigest()

    return FirmwareInfo(
        filename=filename,
        data=data,
        base_address=base_addr,
        total_bytes=len(data),
        crc32=crc,
        md5=md5_hash,
        format=fmt,
    )
