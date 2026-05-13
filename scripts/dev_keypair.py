#!/usr/bin/env python
"""Generate a dev SHARP RSA keypair and a sample signed JWT.

The server runs against the public PEM; tools are called with the signed JWT
in the ``sharp_token`` argument. Production replaces both with a JWKS URL
from Prompt Opinion (see ``.env.example``).

Usage::

    uv run python scripts/dev_keypair.py
    uv run python scripts/dev_keypair.py --patient-id Patient/P123 --ttl-hours 24
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=Path(".secrets"))
    p.add_argument("--patient-id", default="Patient/P123")
    p.add_argument("--encounter-id", default="Encounter/E456")
    p.add_argument("--user-role", default="patient")
    p.add_argument("--audience", default="medrec-superpower")
    p.add_argument("--issuer", default="promptopinion.ai")
    p.add_argument("--ttl-hours", type=int, default=24)
    args = p.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = args.out / "sharp_dev_priv.pem"
    pub_path = args.out / "sharp_dev_pub.pem"
    token_path = args.out / "sharp_dev_token.jwt"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    now = datetime.now(timezone.utc)
    claims = {
        "patient_id": args.patient_id,
        "encounter_id": args.encounter_id,
        "user_role": args.user_role,
        "iss": args.issuer,
        "aud": args.audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=args.ttl_hours)).timestamp()),
        "fhir_token": "stub-fhir-token",
    }
    token = jwt.encode(claims, priv_pem, algorithm="RS256")
    token_path.write_text(token)

    print(f"wrote {priv_path}")
    print(f"wrote {pub_path}")
    print(f"wrote {token_path}")
    print()
    print("Set this env var before starting the server:")
    print(f"  SHARP_PUBLIC_KEY_PEM={pub_path}")
    print()
    print("Token (valid for", args.ttl_hours, "hours):")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
