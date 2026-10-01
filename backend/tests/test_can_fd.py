import pytest
from canviz.config import CANConfig
from canviz.models import SendFrameRequest, CANFrame
from canviz.bus import BusManager
from canviz.routers.replay import _parse_asc, _parse_csv

def test_can_config_fd():
    cfg = CANConfig(
        interface="virtual",
        channel="0",
        bitrate=500_000,
        fd=True,
        data_bitrate=2_000_000,
    )
    assert cfg.fd is True
    assert cfg.data_bitrate == 2_000_000

def test_send_frame_request_fd_validation():
    # 64-byte payload valid for CAN FD
    data_64 = list(range(64))
    req = SendFrameRequest(
        id=0x123,
        dlc=64,
        data=data_64,
        is_fd=True,
        bitrate_switch=True,
    )
    assert req.is_fd is True
    assert req.bitrate_switch is True
    assert len(req.data) == 64

    # 65 bytes should fail validation
    with pytest.raises(ValueError):
        SendFrameRequest(
            id=0x123,
            dlc=65,
            data=list(range(65)),
            is_fd=True,
        )

def test_asc_fd_replay_parsing():
    # Vector ASC CAN FD line sample
    asc_content = (
        "date Sun Sep 20 03:00:00 PM 2026\n"
        "base hex  timestamps absolute\n"
        "   1.234567 CANFD 1 Rx 123   1 0 64 64  " + " ".join(f"{i:02x}" for i in range(64)) + "\n"
    )

    frames = list(_parse_asc(asc_content))
    assert len(frames) == 1
    ts, fid, byte_len, data, is_fd, brs = frames[0]
    assert fid == 0x123
    assert is_fd is True
    assert brs is True
    assert byte_len == 64
    assert len(data) == 64
    assert data == list(range(64))

def test_csv_fd_replay_parsing():
    # CSV with is_fd and bitrate_switch
    csv_content = (
        "timestamp,id,dlc,data,is_extended_id,is_fd,bitrate_switch\n"
        "1.234567,123,64," + "".join(f"{i:02x}" for i in range(64)) + ",0,1,1\n"
    )

    frames = list(_parse_csv(csv_content))
    assert len(frames) == 1
    ts, fid, dlc, data, is_fd, brs = frames[0]
    assert fid == 0x123
    assert is_fd is True
    assert brs is True
    assert dlc == 64
    assert len(data) == 64

@pytest.mark.asyncio
async def test_virtual_bus_fd_connect_and_send():
    mgr = BusManager()
    cfg = CANConfig(interface="virtual", channel="test_vcan_fd", bitrate=500_000, fd=True, data_bitrate=2_000_000)
    await mgr.connect(cfg)
    assert mgr.connected
    assert mgr.is_fd is True

    # Send a 64-byte CAN FD frame
    data_64 = [0xAA] * 64
    await mgr.send(
        arbitration_id=0x7E0,
        data=data_64,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
    )
    await mgr.disconnect()
    assert not mgr.connected

def test_mf4_fd_logging_and_replay(tmp_path):
    import can
    from canviz.routers.replay import _parse_mf4

    mf4_file = tmp_path / "test_fd.mf4"
    writer = can.MF4Writer(str(mf4_file))

    # Classical CAN message
    msg_std = can.Message(
        arbitration_id=0x123,
        data=[1, 2, 3, 4],
        is_extended_id=False,
        is_fd=False,
        timestamp=0.1,
    )
    # CAN FD 64-byte message with BRS
    msg_fd = can.Message(
        arbitration_id=0x7E0,
        data=list(range(64)),
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
        timestamp=0.2,
    )
    writer.on_message_received(msg_std)
    writer.on_message_received(msg_fd)
    writer.stop()

    assert mf4_file.exists()
    assert mf4_file.stat().st_size > 0

    parsed = list(_parse_mf4(mf4_file))
    assert len(parsed) == 2

    # Verify standard message
    ts1, id1, dlc1, data1, fd1, brs1 = parsed[0]
    assert id1 == 0x123
    assert fd1 is False
    assert brs1 is False
    assert data1 == [1, 2, 3, 4]

    # Verify CAN FD message
    ts2, id2, dlc2, data2, fd2, brs2 = parsed[1]
    assert id2 == 0x7E0
    assert fd2 is True
    assert brs2 is True
    assert len(data2) == 64
    assert data2 == list(range(64))
