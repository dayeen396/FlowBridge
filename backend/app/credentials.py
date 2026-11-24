"""Application-layer encryption and per-owner credential resolution."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from app.contracts import ErrorCode
from app.database import CredentialRecord, SessionLocal
from app.nodes.base import NodeFailure


def _fernet() -> Fernet:
    secret = os.getenv("FLOWBRIDGE_SECRET_KEY", "local-dev-only-change-this-secret")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_credential(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data, separators=(",", ":")).encode())


def decrypt_credential(encrypted: bytes) -> dict:
    return json.loads(_fernet().decrypt(encrypted))


class DatabaseCredentialStore:
    def __init__(self, user_id: str):
        self.user_id = UUID(user_id)

    async def get(self, credential_id: str) -> dict:
        try:
            parsed_id = UUID(credential_id)
        except ValueError:
            raise NodeFailure(ErrorCode.CREDENTIAL_ERROR, "credential is unavailable") from None
        with SessionLocal() as db:
            record = db.query(CredentialRecord).filter_by(id=parsed_id, user_id=self.user_id).first()
            if record is None:
                raise NodeFailure(ErrorCode.CREDENTIAL_ERROR, "credential is unavailable")
            encrypted = record.data_encrypted
        try:
            return decrypt_credential(encrypted)
        except (InvalidToken, ValueError, TypeError):
            raise NodeFailure(ErrorCode.CREDENTIAL_ERROR, "credential cannot be decrypted") from None
