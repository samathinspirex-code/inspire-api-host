"""The payments schema is isolated from existing CMS/LMS tables."""
import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.core.config import settings
from app.core.errors import APIError

_pool = None
_lock = asyncio.Lock()
MUTATION_LOCK = 847239610


def json_default(value):
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, UUID): return str(value)
    if hasattr(value, 'isoformat'): return value.isoformat()
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, default=json_default, ensure_ascii=False)


async def codecs(connection):
    await configure(connection)
    await connection.set_type_codec('date', schema='pg_catalog', encoder=str, decoder=str, format='text')
    await connection.set_type_codec('uuid', schema='pg_catalog', encoder=str, decoder=str, format='text')
    for kind in ['json', 'jsonb']:
        await connection.set_type_codec(kind, schema='pg_catalog', encoder=dumps,
                                        decoder=lambda value: json.loads(value, parse_float=Decimal), format='text')


async def configure(connection):
    await connection.execute('SET search_path TO payments, pg_catalog')


async def connect():
    return await asyncpg.connect(user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
                                 host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
                                 ssl='require' if settings.POSTGRES_HOST not in {'localhost', '127.0.0.1', '::1'} else None,
                                 server_settings={'search_path': 'payments,pg_catalog'}, command_timeout=60,
                                 statement_cache_size=0)


async def pool():
    global _pool
    if _pool is None:
        async with _lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
                    host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
                    ssl='require' if settings.POSTGRES_HOST not in {'localhost', '127.0.0.1', '::1'} else None,
                    min_size=0, max_size=settings.PAYMENTS_DATABASE_POOL_SIZE, init=codecs, setup=configure,
                    server_settings={'search_path': 'payments,pg_catalog'}, command_timeout=60, statement_cache_size=0)
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def transaction(write=False):
    try:
        async with (await pool()).acquire(timeout=20) as c:
            async with c.transaction(isolation='read_committed' if write else 'repeatable_read'):
                # Serialise financial writes, including workers, so close runs and
                # gateway retries cannot race a receipt or a period lock.
                if write: await c.execute('SELECT pg_advisory_xact_lock($1)', MUTATION_LOCK)
                yield c
    except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError):
        raise APIError(503, 'PAYMENTS_SETUP_REQUIRED', 'Run the payments migration before using this workspace')
    except asyncpg.UniqueViolationError:
        raise APIError(409, 'PAYMENTS_CONFLICT', 'This record or payment has already been recorded')
    except (asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError):
        raise APIError(409, 'PAYMENTS_LEDGER_REJECTED', 'The ledger refused this change. Check the period and journal balance')
    except (asyncpg.ForeignKeyViolationError, asyncpg.InvalidTextRepresentationError, asyncpg.DataError):
        raise APIError(400, 'PAYMENTS_INVALID_RECORD', 'Check the selected records and field values')


async def rows(c, sql, *args):
    return [dict(r) for r in await c.fetch(sql, *args)]


async def one(c, sql, *args):
    r = await c.fetchrow(sql, *args)
    return dict(r) if r else None


async def audit(c, actor, action, entity, entity_id=None, detail=None):
    await c.execute('INSERT INTO audit_log(actor,action,entity,entity_id,detail) VALUES($1,$2,$3,$4,$5)',
                    actor, action, entity, str(entity_id) if entity_id is not None else None, detail)
