from __future__ import annotations

from datetime import datetime, timedelta

import pyotp
from fastapi import HTTPException, status
from sqlmodel import Session

from app.config import get_settings
from app.models import OtpChallenge
from app.repositories import create_otp_challenge, get_active_otp_challenge
from app.utils.phones import normalize_phone


def assert_admin_phone(whatsapp_phone: str) -> None:
    settings = get_settings()
    normalized_phone = normalize_phone(whatsapp_phone)
    if normalized_phone not in settings.allowed_admin_phone_list:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Numero sem permissao administrativa.")


def start_admin_otp(session: Session, whatsapp_phone: str, purpose: str) -> OtpChallenge:
    assert_admin_phone(whatsapp_phone)
    normalized_phone = normalize_phone(whatsapp_phone)
    secret = pyotp.random_base32()
    challenge = OtpChallenge(
        actor_phone=normalized_phone,
        purpose=purpose,
        secret=secret,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    return create_otp_challenge(session, challenge)


def verify_admin_otp(session: Session, whatsapp_phone: str, purpose: str, otp_code: str) -> OtpChallenge:
    assert_admin_phone(whatsapp_phone)
    challenge = get_active_otp_challenge(session, whatsapp_phone, purpose)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OTP nao encontrado ou expirado.")

    totp = pyotp.TOTP(challenge.secret, interval=600)
    if not totp.verify(otp_code, valid_window=0):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP invalido.")

    challenge.used_at = datetime.utcnow()
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    return challenge


def build_otp_code(secret: str) -> str:
    return pyotp.TOTP(secret, interval=600).now()