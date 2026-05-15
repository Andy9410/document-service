import asyncpg
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from app.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    s = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=s.db_dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool


@asynccontextmanager
async def get_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    async with get_pool().acquire() as conn:
        yield conn
