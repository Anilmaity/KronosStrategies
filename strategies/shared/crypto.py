import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.getenv("FIELD_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _fernet().decrypt(ciphertext.encode()).decode()
