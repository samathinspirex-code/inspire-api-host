"""Isolated PayHere sandbox service. Never start using the production .env."""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv

env_file = Path(os.environ.get('SANDBOX_ENV_FILE', Path(__file__).with_name('.env')))
values = dotenv_values(env_file)
required = ['POSTGRES_PASSWORD', 'PAYMENTS_PAYHERE_MERCHANT_ID', 'PAYMENTS_PAYHERE_MERCHANT_SECRET']
if (values.get('POSTGRES_HOST') != '127.0.0.1'
        or values.get('POSTGRES_DB') != 'inspire_payments_sandbox'
        or values.get('POSTGRES_USER') not in {'inspire_payments_sandbox', 'payments_sandbox'}
        or values.get('PAYMENTS_PAYHERE_MODE') != 'sandbox'
        or values.get('PAYMENTS_MESSAGES_ENABLED', '').lower() != 'false'
        or not all(values.get(key) for key in required)):
    raise RuntimeError('Sandbox settings incomplete or unsafe. Refusing to start.')
origin = urlsplit(values.get('PAYMENTS_PUBLIC_BASE_URL', ''))
if origin.scheme != 'https' or origin.netloc != 'api.inspirecollege.lk' or origin.path.rstrip('/') != '/sandbox-payments' or origin.query or origin.fragment:
    raise RuntimeError('Expected the approved sandbox HTTPS base address.')
load_dotenv(env_file, override=True)

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from app.core.errors import APIError, api_error_handler, request_validation_handler
from app.modules.payments import db, setup
from app.modules.payments.router import public_router

PREFIX = '/sandbox-payments'

@asynccontextmanager
async def lifespan(app):
    yield
    await db.close_pool()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
payment_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
payment_app.add_exception_handler(APIError, api_error_handler)
payment_app.add_exception_handler(RequestValidationError, request_validation_handler)

@payment_app.get('/health')
async def health():
    async with db.transaction() as connection:
        await connection.fetchval('SELECT 1 FROM module_state LIMIT 1')
    return {'status': 'ok', 'mode': 'sandbox', 'messages_enabled': False}

@payment_app.get('/pay/{token}', response_class=HTMLResponse)
async def payment_page(token: str):
    html = (setup.ROOT / 'assets' / 'pay.html').read_text(encoding='utf-8')
    html = html.replace("fetch('/api/v1/payments/", "fetch('/sandbox-payments/api/v1/payments/")
    html = html.replace('Secure fee payment', 'SANDBOX TEST — no real payment')
    return HTMLResponse(html, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'})

# Only student checkout and signed gateway callback routes are exposed.
# No user management, private finance APIs or authentication bypass is mounted.
payment_app.include_router(public_router)
if os.environ.get('SANDBOX_CONSOLE_PASSWORD_HASH'):
    from sandbox_console import install_console
    install_console(payment_app)
app.mount(PREFIX, payment_app)

async def initialize():
    connection = await db.connect()
    try:
        await db.codecs(connection)
        await setup.migrate(connection)
        print('Isolated sandbox schema ready. No sample ledger imported.')
    finally:
        await connection.close()

if __name__ == '__main__':
    if sys.argv[1:] != ['--init']:
        raise SystemExit('Use --init to initialize, or start with uvicorn payments_sandbox:app on loopback.')
    asyncio.run(initialize())
