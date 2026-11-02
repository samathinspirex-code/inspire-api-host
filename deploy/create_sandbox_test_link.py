"""Run over SSH beside payments_sandbox.py; never mounted as a web endpoint."""
import asyncio
import payments_sandbox as sandbox
from app.core.config import settings
from app.modules.payments import billing

async def main():
    async with sandbox.db.transaction(write=True) as connection:
        existing = await connection.fetchrow(
            "SELECT token, status FROM payment_links "
            "WHERE created_by='sandbox-test' AND purpose='Sandbox checkout test' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        if existing:
            result = dict(existing)
        else:
            learner = await billing.create_learner(connection, dict(
                first_name='Sandbox', last_name='Test',
                email='sandbox-test@example.com', phone='+94770000000',
                address='Sandbox test address', city='Colombo',
                lms_ref='payhere-sandbox-checkout-001',
            ), 'sandbox-test')
            result = await billing.create_link(connection, dict(
                learner_id=learner['id'], kind='other', amount='100.00',
                description='Sandbox checkout test', channel='email',
            ), 'sandbox-test')
    # Print after commit so this is a saved link. Reruns reuse it.
    print('Status:', result['status'])
    print('Sandbox test link:')
    print(settings.PAYMENTS_PUBLIC_BASE_URL.rstrip('/')+'/pay/'+result['token'])
    print('Test amount: LKR 100.00. Messages remain disabled.')
    await sandbox.db.close_pool()

if __name__ == '__main__':
    asyncio.run(main())
