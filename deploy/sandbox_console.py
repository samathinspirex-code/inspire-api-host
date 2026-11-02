"""Password-protected access to the real Payments UI on the isolated sandbox."""
import os
import secrets
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.auth.security import verify_password
from app.modules.payments.rate_limit import check
from app.modules.payments.router import router

def install_console(app):
    password_hash = os.environ['SANDBOX_CONSOLE_PASSWORD_HASH']
    csrf = secrets.token_urlsafe(32)
    basic = HTTPBasic(auto_error=False, realm='Inspire payments sandbox')

    async def operator(request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
        valid = credentials is not None
        if valid:
            valid = secrets.compare_digest(credentials.username.encode(), b'sandbox-admin')
            valid = verify_password(credentials.password, password_hash) and valid
        if not valid:
            check(request, 'sandbox-console-login', 10)
            raise HTTPException(401, 'Sandbox sign-in required', headers={'WWW-Authenticate': 'Basic realm="Inspire payments sandbox"'})
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if not secrets.compare_digest(request.headers.get('X-Sandbox-CSRF', ''), csrf):
                raise HTTPException(403, 'Refresh the sandbox console before saving.')
        if request.method == 'POST' and request.url.path.endswith('/api/v1/cms/payments/receipts'):
            if request.headers.get('X-Sandbox-Receipt-Confirmation') != 'confirmed':
                raise HTTPException(409, 'This screen is outdated. Refresh the sandbox console to enter and confirm the receipt amount.')
        return CurrentUser(user_id=900001, email='sandbox-admin@example.com', access=['FINANCE', 'ADMIN'])

    # Applies only to this isolated sub-application, never production auth.
    app.dependency_overrides[get_current_user] = operator
    app.include_router(router)

    @app.get('/console', response_class=HTMLResponse)
    async def console(user=Depends(operator)):
        html = Path(__file__).with_name('sandbox-console.html').read_text(encoding='utf-8')
        html = html.replace('__SANDBOX_CSRF__', csrf).replace('__PAYMENTS_CHANNEL__', secrets.token_urlsafe(24))
        return HTMLResponse(html, headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-src 'self' about:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        })
