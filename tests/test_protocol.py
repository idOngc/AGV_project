"""
协议层单测 —— 只验 pack/unpack 在各种粘包 / 错误对齐场景下的表现。
"""

from app.connectors.seer.protocol import (
    HEADER_SIZE,
    MAX_BODY_LEN,
    START_BYTE,
    AGVProtocol,
)


def test_pack_and_unpack_roundtrip():
    body = {"hello": "world", "n": 42}
    packed = AGVProtocol.pack(req_id=1, msg_type=1001, body=body)

    packets, remaining = AGVProtocol.unpack(packed)
    assert remaining == b""
    assert len(packets) == 1
    req_id, msg_type, body_dict, raw = packets[0]
    assert req_id == 1
    assert msg_type == 1001
    assert body_dict == body
    assert raw == packed


def test_pack_empty_body():
    packed = AGVProtocol.pack(req_id=7, msg_type=1, body=None)
    assert len(packed) == HEADER_SIZE
    packets, remaining = AGVProtocol.unpack(packed)
    assert remaining == b""
    assert packets[0][0] == 7
    assert packets[0][2] == {}


def test_unpack_two_packets_concat():
    """连着发两个包 —— unpack 应一次返回两个。"""
    p1 = AGVProtocol.pack(1, 2001, {"a": 1})
    p2 = AGVProtocol.pack(2, 2002, {"b": 2})

    packets, remaining = AGVProtocol.unpack(p1 + p2)
    assert remaining == b""
    assert len(packets) == 2
    assert packets[0][0] == 1 and packets[0][1] == 2001
    assert packets[1][0] == 2 and packets[1][1] == 2002


def test_unpack_partial_packet():
    """包被切一半 —— 应返回空列表 + 原始剩余 buffer,等后续拼接。"""
    full = AGVProtocol.pack(99, 3001, {"x": "data"})
    partial = full[:-3]
    packets, remaining = AGVProtocol.unpack(partial)
    assert packets == []
    assert remaining == partial


def test_unpack_garbage_then_valid():
    """剩 buffer 前面带业务无关的垃圾字节,应被丢弃并对齐到 START_BYTE。"""
    valid = AGVProtocol.pack(10, 4001, {"k": "v"})
    garbage = b"\x00\x11\x22\x33" + valid
    packets, remaining = AGVProtocol.unpack(garbage)
    assert remaining == b""
    assert len(packets) == 1
    assert packets[0][0] == 10


def test_unpack_oversized_body_len_resyncs():
    """伪造一个 body_len 超过上限的伪起始符,不能冻住解析器。"""
    import struct

    fake_header = struct.pack(
        "!BBHLH6s",
        START_BYTE,
        0x01,
        1,
        MAX_BODY_LEN + 1,
        9999,
        b"\x00" * 6,
    )
    valid = AGVProtocol.pack(20, 5001, {"ok": True})
    packets, _ = AGVProtocol.unpack(fake_header + valid)
    assert any(p[0] == 20 and p[1] == 5001 for p in packets)
