from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.deps import get_current_admin
from app.schemas import ClientUserOut
from app.models_proxy import ClientUser


router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[ClientUserOut])
async def list_users(
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    stmt = select(ClientUser).order_by(ClientUser.id.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())
