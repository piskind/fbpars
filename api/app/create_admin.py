import asyncio
import sys
from app.db import AsyncSessionLocal
from app.models_proxy import AdminUser
from app.security import hash_password


async def main():
    if len(sys.argv) < 3:
        print("Usage: python -m app.create_admin <login> <password>")
        sys.exit(1)

    login, password = sys.argv[1], sys.argv[2]
    async with AsyncSessionLocal() as session:
        user = AdminUser(login=login, password_hash=hash_password(password), is_active=True)
        session.add(user)
        await session.commit()
        print(f"Admin created: id={user.id}, login={login}")


if __name__ == "__main__":
    asyncio.run(main())
