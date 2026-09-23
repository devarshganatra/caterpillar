import asyncio
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert
from backend.app.config import settings
from backend.app.db.models import Machine, User, RoleEnum, UserSiteAccess, Task, TaskStatusEnum
from backend.app.api.auth import get_password_hash
from contracts.machine_config import MACHINES

async def main():
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession)
    
    # We will use stable UUIDs for demo predictability
    operator_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    supervisor_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    admin_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    
    # Demo credentials for seeding
    # DO NOT LOG THE PASSWORD
    demo_password_hash = get_password_hash("demo123")
    
    async with async_session() as session:
        # 1. Machines
        for mid, cfg in MACHINES.items():
            stmt = insert(Machine).values(
                id=mid,
                site_id=cfg.site_id,
                machine_type=cfg.type,
                model=cfg.model
            ).on_conflict_do_update(
                index_elements=["id"],
                set_=dict(site_id=cfg.site_id)
            )
            await session.execute(stmt)
            
        # 2. Users
        users = [
            (operator_id, "operator", RoleEnum.OPERATOR, "Bob Operator"),
            (supervisor_id, "supervisor", RoleEnum.SUPERVISOR, "Alice Supervisor"),
            (admin_id, "admin", RoleEnum.ADMIN, "Eve Admin")
        ]
        for uid, username, role, name in users:
            stmt = insert(User).values(
                id=uid,
                username=username,
                hashed_password=demo_password_hash,
                role=role,
                name=name,
                is_active=True
            ).on_conflict_do_update(
                index_elements=["username"],
                set_=dict(role=role, name=name, is_active=True)
            )
            await session.execute(stmt)
            
        # 3. User Site Access
        # Operator has access to SITE-A
        # Supervisor has access to SITE-A
        # Admin does not need explicit site access, but we'll grant it for completeness
        access_list = [
            (operator_id, "SITE-A"),
            (supervisor_id, "SITE-A"),
            (admin_id, "SITE-A"),
            (admin_id, "SITE-B")
        ]
        for uid, sid in access_list:
            stmt = insert(UserSiteAccess).values(
                user_id=uid,
                site_id=sid
            ).on_conflict_do_nothing()
            await session.execute(stmt)
            
        # 4. Tasks
        tasks = [
            ("TASK-001", operator_id, "EXC001", "SITE-A", TaskStatusEnum.ACTIVE, 120),
            ("TASK-002", operator_id, "LDR001", "SITE-A", TaskStatusEnum.PLANNED, 60),
        ]
        for tid, op_id, mach_id, sid, status, dur in tasks:
            # Application-level validation enforced during seeding:
            # Task site_id MUST match machine site_id
            machine_site_id = MACHINES[mach_id].site_id
            assert sid == machine_site_id, f"Consistency Error: Task {tid} site {sid} != Machine {mach_id} site {machine_site_id}"
            
            stmt = insert(Task).values(
                id=tid,
                operator_id=op_id,
                machine_id=mach_id,
                site_id=sid,
                status=status,
                est_duration_minutes=dur
            ).on_conflict_do_update(
                index_elements=["id"],
                set_=dict(status=status)
            )
            await session.execute(stmt)
            
        await session.commit()
    print("Database seeding completed.")

if __name__ == "__main__":
    asyncio.run(main())
