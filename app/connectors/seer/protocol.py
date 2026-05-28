"""
AGV 协议层 —— 纯函数,负责报文的打包与解析,不涉及网络 IO。

协议格式(大端序,共 16 字节包头):
  | 起始符 1B | 版本号 1B | req_id 2B | body长 4B | 消息类型 2B | 保留 6B |
  后接 body_len 字节的 JSON 正文(UTF-8)

鲁棒性说明:
  - 收到的 buffer 不一定从起始符开始(TCP 粘包 / 丢包 / 异常重启),
    unpack() 会先扫描到第一个 START_BYTE 再解析,前面的垃圾字节直接丢弃。
  - body_len 做上限保护(MAX_BODY_LEN),防止异常数据让解析器等一个巨大的 body。
  - 版本号对不上的包直接丢弃(跳过一个字节继续扫描)。
"""

import json
import struct
import dataclasses

HEADER_FMT = '!BBHLH6s'
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 16
START_BYTE = 0x5A
VERSION = 0x01
RESERVED = b'\x00\x00\x00\x00\x00\x00'

# body_len 的硬上限:防止异常报文让 _recv_loop 无限等待。
# 仙工 AGV 状态 / 动作响应通常 < 4KB,1MB 是非常宽松的安全阈值。
MAX_BODY_LEN = 1 << 20  # 1 MiB


@dataclasses.dataclass
class AGVResponse:
    """一条完整的 AGV 响应包。"""
    agv_name: str
    req_id: int
    msg_type: int
    body: dict
    raw: bytes
    recv_time: float

    def __repr__(self) -> str:
        # raw 原始字节可能很长,日志里只打摘要
        return (
            f"AGVResponse(agv={self.agv_name!r}, req_id={self.req_id}, "
            f"msg_type={self.msg_type}, body={self.body}, "
            f"raw_len={len(self.raw)}, recv_time={self.recv_time:.3f})"
        )


class AGVProtocol:
    """协议打包与解析工具类(全部静态方法)。"""

    @staticmethod
    def pack(req_id: int, msg_type: int, body: dict | None) -> bytes:
        """将 req_id + msg_type + body 字典打包为协议字节串。"""
        if body:
            json_bytes = json.dumps(body).encode('utf-8')
            body_len = len(json_bytes)
        else:
            json_bytes = b''
            body_len = 0

        header = struct.pack(
            HEADER_FMT,
            START_BYTE,
            VERSION,
            req_id,
            body_len,
            msg_type,
            RESERVED,
        )
        return header + json_bytes

    @staticmethod
    def unpack(buffer: bytes) -> tuple[list[tuple[int, int, dict, bytes]], bytes]:
        """
        从 buffer 中尽可能解析出所有完整包。

        返回:
            (packets, remaining_buffer)
            packets 中每个元素为 (req_id, msg_type, body_dict, raw_bytes)

        解析规则:
            1. 若 buffer 首字节不是 START_BYTE,向后扫描直到找到 START_BYTE 为止
               (前面的字节作为"垃圾"丢弃)。
            2. 验证版本号,对不上则视为"伪起始符",跳过一个字节继续扫描。
            3. body_len 超过 MAX_BODY_LEN 视为协议错乱,跳过一字节重新扫描。
            4. buffer 不足 HEADER_SIZE + body_len 时保留等待下次读取。
        """
        packets: list[tuple[int, int, dict, bytes]] = []

        while len(buffer) >= HEADER_SIZE:
            # 对齐到 START_BYTE
            if buffer[0] != START_BYTE:
                idx = buffer.find(bytes([START_BYTE]))
                if idx < 0:
                    # 整块都没有起始符 → 全丢
                    return packets, b''
                buffer = buffer[idx:]
                if len(buffer) < HEADER_SIZE:
                    break

            header = struct.unpack(HEADER_FMT, buffer[:HEADER_SIZE])
            # header: (start, version, req_id, body_len, msg_type, reserved)
            version = header[1]
            req_id = header[2]
            body_len = header[3]
            msg_type = header[4]

            # 校验版本号
            if version != VERSION:
                # 版本不匹配 → 这个起始符是伪的,跳过一字节重扫
                buffer = buffer[1:]
                continue

            # 校验 body_len
            if body_len > MAX_BODY_LEN:
                # 异常 body_len → 协议错乱,跳过一字节重扫
                buffer = buffer[1:]
                continue

            total_len = HEADER_SIZE + body_len
            if len(buffer) < total_len:
                # 数据不足,等下次读取
                break

            # 解析 body
            raw = buffer[:total_len]
            body_dict: dict = {}
            if body_len > 0:
                try:
                    body_dict = json.loads(raw[HEADER_SIZE:total_len].decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # body 解析失败不致命,返回空 dict 由上层决定处理
                    body_dict = {}

            packets.append((req_id, msg_type, body_dict, raw))
            buffer = buffer[total_len:]

        return packets, buffer

    @staticmethod
    def bytes_to_hex(data: bytes) -> str:
        """调试方法:返回十六进制形式的报文字符串。"""
        return ' '.join(f'{b:02X}' for b in data)
