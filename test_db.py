import asyncio
from backend.app.db.session import AsyncSessionLocal
from backend.app.db.models import User, UserSiteAccess, Machine
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

async def main():
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        for u in users:
            print(f"User: {u.username} ({u.role.value})")
            accesses = (await session.execute(select(UserSiteAccess).where(UserSiteAccess.user_id == u.id))).scalars().all()
            print(f"  Access: {[a.site_id for a in accesses]}")
            
        machines = (await session.execute(select(Machine))).scalars().all()
        for m in machines:
            print(f"Machine: {m.id} at {m.site_id}")

asyncio.run(main())
