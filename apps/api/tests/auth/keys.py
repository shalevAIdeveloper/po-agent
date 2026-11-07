"""Test-only signing keys and token builders. No network, keys generated per run."""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

ISSUER = "http://supabase.test/auth/v1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass
class SigningKey:
    kid: str
    algorithm: str
    private_key: Any
    jwk: jwt.PyJWK
    public_jwk: dict[str, Any]

    @property
    def public_pem(self) -> bytes:
        return bytes(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )


def make_es256_key(kid: str = "es-key-1") -> SigningKey:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    public_jwk = {**public_jwk, "kid": kid, "alg": "ES256", "use": "sig"}
    jwk = jwt.PyJWK.from_dict(public_jwk)
    return SigningKey(kid, "ES256", private_key, jwk, public_jwk)


def make_rs256_key(kid: str = "rs-key-1") -> SigningKey:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk = {**public_jwk, "kid": kid, "alg": "RS256", "use": "sig"}
    jwk = jwt.PyJWK.from_dict(public_jwk)
    return SigningKey(kid, "RS256", private_key, jwk, public_jwk)


@dataclass
class StaticKeys:
    """KeySource backed by a fixed set of keys."""

    keys: dict[str, jwt.PyJWK] = field(default_factory=dict)
    lookups: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, *signing_keys: SigningKey) -> "StaticKeys":
        return cls({key.kid: key.jwk for key in signing_keys})

    async def get_key(self, kid: str) -> jwt.PyJWK | None:
        self.lookups.append(kid)
        return self.keys.get(kid)


def claims(user_id: UUID | None = None, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base: dict[str, Any] = {
        "sub": str(user_id or uuid4()),
        "aud": "authenticated",
        "iss": ISSUER,
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
        "is_anonymous": False,
        "aal": "aal1",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def sign(key: SigningKey, payload: dict[str, Any], **header: Any) -> str:
    # A None value drops that header field (e.g. kid=None → token without a kid).
    headers = {
        name: value for name, value in {"kid": key.kid, **header}.items() if value is not None
    }
    return jwt.encode(payload, key.private_key, algorithm=key.algorithm, headers=headers)


def forge_hs256_with_public_key(key: SigningKey, payload: dict[str, Any]) -> str:
    """Algorithm-confusion attack: HMAC-sign with the server's PUBLIC key as the secret."""
    header = {"alg": "HS256", "typ": "JWT", "kid": key.kid}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    signature = hmac.new(key.public_pem, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def forge_alg_none(key: SigningKey, payload: dict[str, Any]) -> str:
    header = {"alg": "none", "typ": "JWT", "kid": key.kid}
    return f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}."


def tamper_payload(token: str, **changes: Any) -> str:
    """Keeps header and signature, swaps in a modified payload."""
    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded.update(changes)
    return f"{header}.{_b64(json.dumps(decoded).encode())}.{signature}"
