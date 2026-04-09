"""Low-level ctypes bindings for CommonCrypto AES-256-GCM — no third-party deps.

Using ctypes to call CCCryptorGCMOneshotEncrypt / CCCryptorGCMOneshotDecrypt
from libSystem (which re-exports CommonCrypto on macOS >= 10.13).

Note: these functions are defined in CommonCryptorSPI.h (System Private
Interface) rather than the public CommonCryptor.h, but they have been stable
and widely used since macOS 10.13 (2017).
"""

from __future__ import annotations

import ctypes
import ctypes.util
from ctypes import c_int32, c_size_t, c_uint32, c_void_p

# ---------------------------------------------------------------------------
# Load framework
# ---------------------------------------------------------------------------
_lib_path = ctypes.util.find_library("System")
if _lib_path is None:  # pragma: no cover
    raise OSError("Cannot find libSystem — are you running on macOS?")
_cc = ctypes.cdll.LoadLibrary(_lib_path)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_kCCAlgorithmAES = 0
_kCCSuccess = 0

_TAG_LENGTH = 16

# ---------------------------------------------------------------------------
# Function signatures
# ---------------------------------------------------------------------------

# CCCryptorStatus CCCryptorGCMOneshotEncrypt(
#     CCAlgorithm alg,
#     const void *key,    size_t keyLength,
#     const void *iv,     size_t ivLength,
#     const void *aData,  size_t aDataLength,
#     const void *dataIn, size_t dataInLength,
#     void *dataOut,
#     void *tagOut,       size_t tagLength)
_cc.CCCryptorGCMOneshotEncrypt.restype = c_int32
_cc.CCCryptorGCMOneshotEncrypt.argtypes = [
    c_uint32,            # alg
    c_void_p, c_size_t,  # key, keyLength
    c_void_p, c_size_t,  # iv, ivLength
    c_void_p, c_size_t,  # aData, aDataLength
    c_void_p, c_size_t,  # dataIn, dataInLength
    c_void_p,            # dataOut
    c_void_p, c_size_t,  # tagOut, tagLength
]

# CCCryptorStatus CCCryptorGCMOneshotDecrypt(
#     CCAlgorithm alg,
#     const void *key,    size_t keyLength,
#     const void *iv,     size_t ivLength,
#     const void *aData,  size_t aDataLength,
#     const void *dataIn, size_t dataInLength,
#     void *dataOut,
#     const void *tag,    size_t tagLength)
_cc.CCCryptorGCMOneshotDecrypt.restype = c_int32
_cc.CCCryptorGCMOneshotDecrypt.argtypes = [
    c_uint32,            # alg
    c_void_p, c_size_t,  # key, keyLength
    c_void_p, c_size_t,  # iv, ivLength
    c_void_p, c_size_t,  # aData, aDataLength
    c_void_p, c_size_t,  # dataIn, dataInLength
    c_void_p,            # dataOut
    c_void_p, c_size_t,  # tag, tagLength
]


# ---------------------------------------------------------------------------
# Python API
# ---------------------------------------------------------------------------


class CryptoError(Exception):
    """Raised when a CommonCrypto operation fails."""


def aes_gcm_encrypt(
    key: bytes,
    nonce: bytes,
    plaintext: bytes,
    aad: bytes,
) -> bytes:
    """AES-256-GCM encrypt.  Returns ``ciphertext || 16-byte auth tag``.

    This matches the output layout of ``cryptography.hazmat`` AESGCM.encrypt():
    the returned bytes are ciphertext (same length as *plaintext*) followed by
    a 16-byte authentication tag.
    """
    if len(key) != 32:
        raise CryptoError(f"key must be 32 bytes, got {len(key)}")
    if len(nonce) != 12:
        raise CryptoError(f"nonce must be 12 bytes, got {len(nonce)}")

    ct_len = len(plaintext)
    ct_buf = ctypes.create_string_buffer(ct_len) if ct_len else None
    tag_buf = ctypes.create_string_buffer(_TAG_LENGTH)

    status = _cc.CCCryptorGCMOneshotEncrypt(
        _kCCAlgorithmAES,
        key, len(key),
        nonce, len(nonce),
        aad if aad else None, len(aad),
        plaintext if plaintext else None, ct_len,
        ct_buf,
        tag_buf, _TAG_LENGTH,
    )
    if status != _kCCSuccess:
        raise CryptoError(f"CCCryptorGCMOneshotEncrypt failed: status={status}")

    ct_bytes = ct_buf.raw if ct_buf is not None else b""
    return ct_bytes + tag_buf.raw


def aes_gcm_decrypt(
    key: bytes,
    nonce: bytes,
    ciphertext_and_tag: bytes,
    aad: bytes,
) -> bytes:
    """AES-256-GCM decrypt.  *ciphertext_and_tag* = ``ciphertext || 16-byte tag``.

    This matches the input layout of ``cryptography.hazmat`` AESGCM.decrypt().
    Raises :class:`CryptoError` on authentication failure or any other error.
    """
    if len(key) != 32:
        raise CryptoError(f"key must be 32 bytes, got {len(key)}")
    if len(nonce) != 12:
        raise CryptoError(f"nonce must be 12 bytes, got {len(nonce)}")
    if len(ciphertext_and_tag) < _TAG_LENGTH:
        raise CryptoError("ciphertext_and_tag too short")

    ct = ciphertext_and_tag[:-_TAG_LENGTH]
    tag = ciphertext_and_tag[-_TAG_LENGTH:]

    ct_len = len(ct)
    pt_buf = ctypes.create_string_buffer(ct_len) if ct_len else None

    status = _cc.CCCryptorGCMOneshotDecrypt(
        _kCCAlgorithmAES,
        key, len(key),
        nonce, len(nonce),
        aad if aad else None, len(aad),
        ct if ct else None, ct_len,
        pt_buf,
        tag, _TAG_LENGTH,
    )
    if status != _kCCSuccess:
        raise CryptoError(
            f"CCCryptorGCMOneshotDecrypt failed: status={status}"
        )

    return pt_buf.raw if pt_buf is not None else b""
