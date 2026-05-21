from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.security import decode_token
from app.models_proxy import AdminUser, ClientUser


admin_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
client_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/client/login")


async def get_current_admin(
    token: str = Depends(admin_oauth2),
    session: AsyncSession = Depends(get_session),
) -> AdminUser:
    payload = decode_token(token)
    if not payload or payload.get("kind") not in (None, "admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No subject")
    stmt = select(AdminUser).where(AdminUser.id == int(sub), AdminUser.is_active.is_(True))
    user = (await session.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_client(
    token: str = Depends(client_oauth2),
    session: AsyncSession = Depends(get_session),
) -> ClientUser:
    payload = decode_token(token)
    if not payload or payload.get("kind") != "client":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No subject")
    stmt = select(ClientUser).where(ClientUser.id == int(sub))
    user = (await session.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not verified")
    return user