"""
Async SQLAlchemy session factory.

The async engine uses asyncpg. The sync URL (psycopg2) is only used by alembic.
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
import sys
from backend.app.config import settings

pool_opts = {"poolclass": NullPool} if "pytest" in sys.modules else {"pool_size": 5, "max_overflow": 10}

engine = create_async_engine(
    settings.database_url,
    **pool_opts,
    pool_pre_ping=True,      # Detect stale connections before use
    echo=False,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
