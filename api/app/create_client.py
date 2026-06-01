import asyncio
import sys
from app.db import AsyncSessionLocal
from app.models_proxy import ClientUser
from app.security import hash_password


async def main():
    if len(sys.argv) < 3:
        print("Usage: python -m app.create_client <email> <password>")
        sys.exit(1)

    email, password = sys.argv[1].strip().lower(), sys.argv[2]
    if len(password) < 6:
        print("Password must be at least 6 characters")
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        user = ClientUser(
            email=email,
            password_hash=hash_password(password),
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        print(f"Client created: id={user.id}, email={email}")


if __name__ == "__main__":
    asyncio.run(main())
