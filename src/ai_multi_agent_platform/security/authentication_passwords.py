"""Memory-hard local password hashing for authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


class ScryptPasswordHasher:
    """Dependency-free memory-hard password verifier using Python/OpenSSL scrypt."""

    def __init__(
        self,
        *,
        n: int = 2**15,
        r: int = 8,
        p: int = 1,
        dklen: int = 32,
        maxmem: int = 64 * 1024 * 1024,
    ) -> None:
        self._n = n
        self._r = r
        self._p = p
        self._dklen = dklen
        self._maxmem = maxmem

    def hash(self, password: str) -> str:
        validate_password(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self._n,
            r=self._r,
            p=self._p,
            dklen=self._dklen,
            maxmem=self._maxmem,
        )
        encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
        encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        parameters = f"n={self._n},r={self._r},p={self._p},dklen={self._dklen}"
        return f"scrypt${parameters}${encoded_salt}${encoded_digest}"

    def verify(self, password: str, verifier: str) -> bool:
        try:
            algorithm, parameters, encoded_salt, encoded_digest = verifier.split("$", 3)
            if algorithm != "scrypt":
                return False
            values = parse_parameters(parameters)
            salt = decode_base64(encoded_salt)
            expected = decode_base64(encoded_digest)
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=values["n"],
                r=values["r"],
                p=values["p"],
                dklen=values["dklen"],
                maxmem=self._maxmem,
            )
        except (ValueError, KeyError):
            return False
        return hmac.compare_digest(actual, expected)


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("local passwords must contain at least 12 characters")
    if len(password.encode("utf-8")) > 1024:
        raise ValueError("local password is too large")


def parse_parameters(value: str) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for item in value.split(","):
        name, raw = item.split("=", 1)
        parsed[name] = int(raw)
    return parsed


def decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


__all__ = ["ScryptPasswordHasher"]
