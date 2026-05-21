import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import os

from app.db import get_session
from app.security import hash_password, verify_password, create_token
from app.schemas import (
    ClientSignupIn,
    ClientLoginIn,
    ClientSignupOut,
    ClientUserMe,
    TokenOut,
)
from app.deps import get_current_client
from app.models_proxy import ClientUser


router = APIRouter(prefix="/api/client", tags=["client_auth"])


@router.post("/signup", response_model=ClientSignupOut)
async def signup(data: ClientSignupIn, session: AsyncSession = Depends(get_session)):
    email = data.email.strip().lower()
    if not email or "@" not in email or len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Invalid email or password too short")
    existing = (await session.execute(
        select(ClientUser).where(ClientUser.email == email)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    token = secrets.token_urlsafe(32)
    user = ClientUser(
        email=email,
        password_hash=hash_password(data.password),
        verification_token=token,
        email_verified=False,
        referral_source=data.referral_source,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    return ClientSignupOut(
        id=user.id,
        email=user.email,
        verification_url=f"{frontend_base}/verify/{token}",
    )


@router.post("/resend-verification")
async def resend_verification(data: ClientLoginIn, session: AsyncSession = Depends(get_session)):
    email = data.email.strip().lower()
    user = (await session.execute(
        select(ClientUser).where(ClientUser.email == email)
    )).scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    new_token = secrets.token_urlsafe(32)
    user.verification_token = new_token
    await session.commit()
    frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    return {"ok": True, "verification_url": f"{frontend_base}/verify/{new_token}"}


@router.get("/verify/{token}")
async def verify_email(token: str, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(
        select(ClientUser).where(ClientUser.verification_token == token)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid token")
    user.email_verified = True
    await session.commit()
    return {"ok": True, "email": user.email}


@router.post("/login", response_model=TokenOut)
async def login(data: ClientLoginIn, session: AsyncSession = Depends(get_session)):
    email = data.email.strip().lower()
    user = (await session.execute(
        select(ClientUser).where(ClientUser.email == email)
    )).scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Email not verified")

    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    return TokenOut(access_token=create_token(user.id, kind="client"))


@router.get("/me", response_model=ClientUserMe)
async def me(current: ClientUser = Depends(get_current_client)):
    return current