import os
import base64
import hashlib
import secrets
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend


class CryptoService:
    NONCE_SIZE = 12
    KEY_SIZE = 32
    
    def __init__(self, key: Optional[bytes] = None):
        if key is None:
            self._key = secrets.token_bytes(self.KEY_SIZE)
        else:
            if len(key) != self.KEY_SIZE:
                raise ValueError(f"Key must be {self.KEY_SIZE} bytes")
            self._key = key
        self._aesgcm = AESGCM(self._key)
    
    @classmethod
    def from_key_file(cls, key_file: str) -> 'CryptoService':
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                key = f.read()
            if len(key) != cls.KEY_SIZE:
                key = hashlib.sha256(key).digest()
            return cls(key)
        else:
            instance = cls()
            os.makedirs(os.path.dirname(key_file), exist_ok=True)
            with open(key_file, 'wb') as f:
                f.write(instance._key)
            return instance
    
    @classmethod
    def from_password(cls, password: str) -> 'CryptoService':
        key = hashlib.sha256(password.encode()).digest()
        return cls(key)
    
    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext
    
    def decrypt(self, encrypted: bytes) -> bytes:
        if len(encrypted) < self.NONCE_SIZE + 16:
            raise ValueError("Encrypted data too short")
        nonce = encrypted[:self.NONCE_SIZE]
        ciphertext = encrypted[self.NONCE_SIZE:]
        return self._aesgcm.decrypt(nonce, ciphertext, None)
    
    def encrypt_string(self, plaintext: str) -> str:
        encrypted = self.encrypt(plaintext.encode('utf-8'))
        return base64.b64encode(encrypted).decode('ascii')
    
    def decrypt_string(self, encrypted_b64: str) -> str:
        encrypted = base64.b64decode(encrypted_b64.encode('ascii'))
        decrypted = self.decrypt(encrypted)
        return decrypted.decode('utf-8')
    
    def get_key(self) -> bytes:
        return self._key
    
    def compute_hmac(self, data: bytes) -> str:
        return hashlib.sha256(self._key + data).hexdigest()[:32]
    
    def verify_hmac(self, data: bytes, hmac: str) -> bool:
        return self.compute_hmac(data) == hmac


def generate_key() -> bytes:
    return secrets.token_bytes(CryptoService.KEY_SIZE)


def derive_key_from_password(password: str, salt: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    if salt is None:
        salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000, dklen=32)
    return key, salt
