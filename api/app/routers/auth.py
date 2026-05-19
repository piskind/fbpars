from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.security import verify_password, create_token
from app.schemas import AdminLoginIn, TokenOut, AdminUserOut
from app.deps import get_current_admin
from app.models_proxy import AdminUser


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(data: AdminLoginIn, session: AsyncSession = Depends(get_session)):
    stmt = select(AdminUser).where(AdminUser.login == data.login, AdminUser.is_active.is_(True))
    user = (await session.execute(stmt)).scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=AdminUserOut)
async def me(current: AdminUser = Depends(get_current_admin)):
    return current
