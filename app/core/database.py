from collections.abc import AsyncGenerator
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# A transaction pooler may give each transaction a different server connection,
# so cached or reused prepared statement names are not safe there.
_transaction_pooler_args = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
}

engine = create_async_engine(
    settings.DATABASE_URL,
    connect_args=_transaction_pooler_args if settings.DATABASE_TRANSACTION_PORT else {},
    echo=settings.ENVIRONMENT == "development",
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_POOL_TIMEOUT_SECONDS,
    pool_recycle=settings.DATABASE_POOL_RECYCLE_SECONDS,
    pool_pre_ping=True,
    pool_use_lifo=True,
)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
