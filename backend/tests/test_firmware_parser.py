import pytest
from canviz.flasher.firmware_parser import (
    parse_firmware,
    parse_intel_hex,
    parse_srec,
)

def test_parse_binary_file():
    raw_bytes = bytes([i % 256 for i in range(256)])
    parsed = parse_firmware("firmware.bin", raw_bytes, default_base_address=0x08000000)

    assert parsed.filename == "firmware.bin"
    assert parsed.format == "Raw Binary"
    assert parsed.total_bytes == 256
    assert parsed.base_address == 0x08000000
    assert len(parsed.data) == 256
    assert parsed.data == raw_bytes
    assert parsed.crc32.startswith("0x")
    assert len(parsed.md5) == 32

def test_parse_intel_hex():
    # Intel HEX sample:
    # :020000040800F2 (Extended Linear Address: 0x08000000)
    # :10000000000102030405060708090A0B0C0D0E0F78 (16 bytes data at 0x08000000)
    # :00000001FF (EOF)
    hex_content = (
        ":020000040800F2\n"
        ":10000000000102030405060708090A0B0C0D0E0F78\n"
        ":00000001FF\n"
    ).encode("ascii")

    base_addr, data = parse_intel_hex(hex_content.decode("ascii"))
    assert base_addr == 0x08000000
    assert len(data) == 16
    assert data == bytes(range(16))

    # Test via full parser
    parsed = parse_firmware("app.hex", hex_content)
    assert parsed.format == "Intel HEX"
    assert parsed.base_address == 0x08000000
    assert parsed.total_bytes == 16
    assert parsed.crc32.startswith("0x")
    assert len(parsed.md5) == 32

def test_parse_srec():
    # Motorola S-Record sample:
    # S0030000FC (Header)
    # S31508000000000102030405060708090A0B0C0D0E0F6B (21 bytes: 1 count + 4 addr + 16 data + 1 chk)
    # S70508000000EC (Termination)
    srec_content = (
        "S0030000FC\n"
        "S31508000000000102030405060708090A0B0C0D0E0F6A\n"
        "S70508000000F2\n"
    ).encode("ascii")

    base_addr, data = parse_srec(srec_content.decode("ascii"))
    assert base_addr == 0x08000000
    assert len(data) == 16
    assert data == bytes(range(16))

    parsed = parse_firmware("bootloader.srec", srec_content)
    assert parsed.format == "Motorola S-Record"
    assert parsed.base_address == 0x08000000
    assert parsed.total_bytes == 16
    assert parsed.crc32.startswith("0x")
    assert len(parsed.md5) == 32
