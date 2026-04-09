"""Tests for wenzi._commoncrypto — AES-256-GCM via CommonCrypto ctypes."""

import base64
import os

import pytest

from wenzi._commoncrypto import CryptoError, aes_gcm_decrypt, aes_gcm_encrypt


class TestAesGcmRoundtrip:
    def test_basic_roundtrip(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        aad = b"some-key"
        plaintext = b"hello world"
        ct = aes_gcm_encrypt(key, nonce, plaintext, aad)
        assert len(ct) == len(plaintext) + 16
        result = aes_gcm_decrypt(key, nonce, ct, aad)
        assert result == plaintext

    def test_empty_plaintext(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        ct = aes_gcm_encrypt(key, nonce, b"", b"key")
        assert len(ct) == 16  # tag only
        result = aes_gcm_decrypt(key, nonce, ct, b"key")
        assert result == b""

    def test_empty_aad(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        plaintext = b"data"
        ct = aes_gcm_encrypt(key, nonce, plaintext, b"")
        result = aes_gcm_decrypt(key, nonce, ct, b"")
        assert result == plaintext

    def test_large_plaintext(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        plaintext = os.urandom(64 * 1024)  # 64 KB
        ct = aes_gcm_encrypt(key, nonce, plaintext, b"aad")
        result = aes_gcm_decrypt(key, nonce, ct, b"aad")
        assert result == plaintext

    def test_unicode_roundtrip_via_vault_format(self):
        """Simulate vault encrypt/decrypt with Unicode content."""
        key = b"A" * 32
        nonce = os.urandom(12)
        value = "你好世界"
        aad = "ai_enhance.providers.openai.api_key"
        ct = aes_gcm_encrypt(key, nonce, value.encode("utf-8"), aad.encode("utf-8"))
        blob = base64.b64encode(nonce + ct).decode("ascii")
        # Decrypt from wire format
        raw = base64.b64decode(blob)
        result = aes_gcm_decrypt(key, raw[:12], raw[12:], aad.encode("utf-8"))
        assert result.decode("utf-8") == value


class TestAesGcmAuthFailures:
    def test_tampered_ciphertext(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        ct = aes_gcm_encrypt(key, nonce, b"secret", b"aad")
        tampered = bytes([ct[0] ^ 0xFF]) + ct[1:]
        with pytest.raises(CryptoError):
            aes_gcm_decrypt(key, nonce, tampered, b"aad")

    def test_tampered_tag(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        ct = aes_gcm_encrypt(key, nonce, b"secret", b"aad")
        tampered = ct[:-1] + bytes([ct[-1] ^ 0xFF])
        with pytest.raises(CryptoError):
            aes_gcm_decrypt(key, nonce, tampered, b"aad")

    def test_wrong_aad(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        ct = aes_gcm_encrypt(key, nonce, b"secret", b"correct-aad")
        with pytest.raises(CryptoError):
            aes_gcm_decrypt(key, nonce, ct, b"wrong-aad")

    def test_wrong_key(self):
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        nonce = os.urandom(12)
        ct = aes_gcm_encrypt(key1, nonce, b"secret", b"aad")
        with pytest.raises(CryptoError):
            aes_gcm_decrypt(key2, nonce, ct, b"aad")

    def test_wrong_nonce(self):
        key = os.urandom(32)
        nonce1 = os.urandom(12)
        nonce2 = os.urandom(12)
        ct = aes_gcm_encrypt(key, nonce1, b"secret", b"aad")
        with pytest.raises(CryptoError):
            aes_gcm_decrypt(key, nonce2, ct, b"aad")


class TestAesGcmInputValidation:
    def test_invalid_key_length(self):
        with pytest.raises(CryptoError, match="key must be 32 bytes"):
            aes_gcm_encrypt(b"short", os.urandom(12), b"data", b"aad")

    def test_invalid_nonce_length(self):
        with pytest.raises(CryptoError, match="nonce must be 12 bytes"):
            aes_gcm_encrypt(os.urandom(32), b"short", b"data", b"aad")

    def test_ciphertext_too_short(self):
        with pytest.raises(CryptoError, match="too short"):
            aes_gcm_decrypt(os.urandom(32), os.urandom(12), b"short", b"aad")

    def test_decrypt_invalid_key_length(self):
        with pytest.raises(CryptoError, match="key must be 32 bytes"):
            aes_gcm_decrypt(b"short", os.urandom(12), os.urandom(32), b"aad")

    def test_decrypt_invalid_nonce_length(self):
        with pytest.raises(CryptoError, match="nonce must be 12 bytes"):
            aes_gcm_decrypt(os.urandom(32), b"short", os.urandom(32), b"aad")


class TestBackwardCompatibility:
    """Verify that blobs produced by the old cryptography library
    can be decrypted by the new CommonCrypto implementation.

    Test vector generated with::

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = b'A' * 32
        nonce = bytes(range(12))
        aad = b'test.api_key'
        plaintext = b'sk-test-12345'
        ct = AESGCM(key).encrypt(nonce, plaintext, aad)
        blob = base64.b64encode(nonce + ct).decode('ascii')
    """

    _KNOWN_BLOB = "AAECAwQFBgcICQoLPcbbRbOF/g5fVrHB8zJzENOiEpKe1A23XI8SWw0="
    _KNOWN_KEY = b"A" * 32
    _KNOWN_AAD = b"test.api_key"
    _KNOWN_PLAINTEXT = b"sk-test-12345"

    def test_decrypt_cryptography_generated_blob(self):
        raw = base64.b64decode(self._KNOWN_BLOB)
        nonce = raw[:12]
        ct = raw[12:]
        result = aes_gcm_decrypt(self._KNOWN_KEY, nonce, ct, self._KNOWN_AAD)
        assert result == self._KNOWN_PLAINTEXT

    def test_encrypt_produces_identical_output(self):
        """Given identical key/nonce/aad/plaintext, CommonCrypto produces
        the same blob as the cryptography library did."""
        nonce = bytes(range(12))
        ct = aes_gcm_encrypt(
            self._KNOWN_KEY, nonce, self._KNOWN_PLAINTEXT, self._KNOWN_AAD
        )
        blob = base64.b64encode(nonce + ct).decode("ascii")
        assert blob == self._KNOWN_BLOB
