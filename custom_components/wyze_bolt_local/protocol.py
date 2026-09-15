from .crypto import aes_ecb_encrypt

# The Bolt uses CRC-16/IBM (reversed polynomial 0xA001) with initial value 0.
def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF

def pack_l1(flags: int, seq: int, payload: bytes) -> bytes:
    return (
        b"\xAB"
        + bytes([flags & 0xFF])
        + len(payload).to_bytes(2, "big")
        + crc16(payload).to_bytes(2, "big")
        + seq.to_bytes(2, "big")
        + payload
    )

def pack_l2_dict(cmd: int, flags: int, fields: dict[int, bytes]) -> bytes:
    out = bytes([cmd & 0xFF, flags & 0xFF])
    for key, value in fields.items():
        out += bytes([key & 0xFF]) + len(value).to_bytes(2, "big") + value
    return out

def pack_challenge_request() -> bytes:
    # 91 00 / key 0x0A / value 0x27
    return pack_l1(0, 1, pack_l2_dict(0x91, 0, {0x0A: b"\x27"}))

def pack_ack(seq: int) -> bytes:
    return pack_l1(0x08, seq, b"")

def pack_lock_unlock(ble_id: int, operate_key: str, challenge: bytes, command: str, seq: int = 2) -> bytes:
    if len(challenge) != 16:
        raise ValueError(f"Expected 16-byte D2 challenge, got {len(challenge)}")
    if command == "unlock":
        magic = bytes.fromhex("01000000000000000000006c6f6f636b")
    elif command == "lock":
        magic = bytes.fromhex("02000000000000000000006c6f6f636b")
    else:
        raise ValueError(command)
    encrypted = aes_ecb_encrypt(operate_key, challenge)
    response = bytes(a ^ b for a, b in zip(encrypted, magic))
    l2 = (
        bytes.fromhex("0400050002")
        + ble_id.to_bytes(2, "big")
        + bytes.fromhex("040010")
        + response
        + bytes.fromhex("ad000100f4000101f7000101")
    )
    return pack_l1(0, seq, l2)

def parse_l2_dict(payload: bytes) -> tuple[int, int, dict[int, bytes]]:
    if len(payload) < 2:
        raise ValueError("Short L2 payload")
    cmd, flags = payload[0], payload[1]
    fields: dict[int, bytes] = {}
    pos = 2
    while pos < len(payload):
        if pos + 3 > len(payload):
            raise ValueError("Truncated L2 field")
        key = payload[pos]
        length = int.from_bytes(payload[pos + 1:pos + 3], "big")
        pos += 3
        if pos + length > len(payload):
            raise ValueError("Truncated L2 value")
        fields[key] = payload[pos:pos + length]
        pos += length
    return cmd, flags, fields

def extract_l1_frames(buffer: bytearray) -> list[tuple[int, int, bytes]]:
    frames = []
    while True:
        if not buffer:
            break
        try:
            start = buffer.index(0xAB)
        except ValueError:
            buffer.clear()
            break
        if start:
            del buffer[:start]
        if len(buffer) < 8:
            break
        length = int.from_bytes(buffer[2:4], "big")
        total = 8 + length
        if len(buffer) < total:
            break
        frame = bytes(buffer[:total])
        del buffer[:total]
        payload = frame[8:]
        expected = int.from_bytes(frame[4:6], "big")
        if crc16(payload) != expected:
            raise ValueError(f"CRC mismatch: expected {expected:04x}, got {crc16(payload):04x}")
        frames.append((frame[1], int.from_bytes(frame[6:8], "big"), payload))
    return frames
