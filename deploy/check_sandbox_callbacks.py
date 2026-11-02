"""SSH-only checks against the guarded sandbox; never sends a payment to PayHere."""
import asyncio
import httpx
import payments_sandbox as sandbox
from app.core.config import settings
from app.modules.payments import payhere

async def financial_state(c):
    return dict(await c.fetchrow("""
        SELECT (SELECT count(*) FROM payments) AS receipts,
               (SELECT coalesce(sum(amount),0) FROM payments) AS received,
               (SELECT count(*) FROM journals) AS journals,
               (SELECT coalesce(sum(amount_paid),0) FROM invoices) AS invoice_paid
    """))

async def main(client=None):
    c = await sandbox.db.connect()
    try:
        await sandbox.db.codecs(c)
        event = await c.fetchrow("""
            SELECT g.raw FROM gateway_events g
            JOIN checkout_orders o ON o.order_id = g.order_id
            JOIN payment_links p ON p.id = o.link_id
            WHERE g.outcome='posted' AND g.signature_valid=true
              AND p.created_by='sandbox-test'
            ORDER BY g.id DESC LIMIT 1
        """)
        if not event:
            raise RuntimeError('A verified successful sandbox payment is required first.')
        raw = event['raw']
        keys = ['merchant_id','order_id','payhere_amount','payhere_currency','status_code']
        body = {key: str(raw[key]) for key in keys + ['payment_id']}
        body['md5sig'] = payhere.md5(''.join(body[key] for key in keys)
                                   + payhere.md5(settings.PAYMENTS_PAYHERE_MERCHANT_SECRET))
        url = settings.PAYMENTS_PUBLIC_BASE_URL.rstrip('/') + '/api/v1/payments/payhere/notify'
        before = await financial_state(c)

        async def exercise(http):
            for label, payload, outcome, valid in [
                ('Duplicate notification', body, 'duplicate', True),
                ('Invalid signature', dict(body, md5sig='0'*32), 'rejected', False),
            ]:
                marker = await c.fetchval('SELECT coalesce(max(id),0) FROM gateway_events')
                response = await http.post(url, data=payload)
                response.raise_for_status()
                result = await c.fetchrow("""
                    SELECT outcome, signature_valid FROM gateway_events
                    WHERE id>$1 AND order_id=$2 ORDER BY id DESC LIMIT 1
                """, marker, body['order_id'])
                if not result or result['outcome'] != outcome or result['signature_valid'] != valid:
                    raise RuntimeError(label + ': expected gateway result not recorded')
                if await financial_state(c) != before:
                    raise RuntimeError(label + ': financial records changed unexpectedly')
                print(label + ': PASS')

        if client is None:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
                await exercise(http)
        else:
            await exercise(client)
        print('Receipts, receipt totals, invoice payments and journal count unchanged: PASS')
    finally:
        await c.close()
        await sandbox.db.close_pool()

if __name__ == '__main__':
    asyncio.run(main())
