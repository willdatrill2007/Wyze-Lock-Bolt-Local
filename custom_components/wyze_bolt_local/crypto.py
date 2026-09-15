from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def aes_ecb_encrypt(key: str, data: bytes) -> bytes:
    return Cipher(algorithms.AES(key.encode("ascii")), modes.ECB()).encryptor().update(data)

def aes_ecb_decrypt(key: str, data: bytes) -> bytes:
    return Cipher(algorithms.AES(key.encode("ascii")), modes.ECB()).decryptor().update(data)

def decrypt_state_or_battery(key: str, data: bytes) -> tuple[int, int]:
    if len(data) != 16:
        raise ValueError(f"Expected 16 encrypted bytes, got {len(data)}")
    plain = aes_ecb_decrypt(key, data)
    if plain[11:16] != b"loock":
        raise ValueError("Wyze Bolt key validation marker missing")
    return plain[0], int.from_bytes(plain[1:5], "big")
