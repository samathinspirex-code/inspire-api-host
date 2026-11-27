"""After money is recorded in Accounts, update the student's CRM lead (payment fields, timeline, stage Enrolled).

Runs after the Accounts transaction has committed, so a CRM problem can never undo a receipt. Orders that could not be
synced keep crm_synced_at empty and are retried by scripts/run_website_worker.py.
"""
import logging

from app.core.database import AsyncSessionLocal
from app.modules.payments.db import rows, transaction
from . import leads

logger = logging.getLogger(__name__)


async def sync_paid_orders(limit: int = 20, reference: str | None = None) -> int:
    async with transaction() as c:
        todo = await rows(c, """SELECT o.id, o.reference, o.crm_lead_id, o.method, o.amount, o.paid_at, l.learner_no,
                COALESCE((SELECT array_agg(v.number ORDER BY v.created_at) FROM invoices v JOIN web_enrolments w ON w.enrolment_id=v.enrolment_id
                          WHERE w.web_order_id=o.id), '{}') AS invoices
            FROM web_orders o JOIN learners l ON l.id=o.learner_id
            WHERE o.status='paid' AND o.crm_synced_at IS NULL AND o.crm_lead_id IS NOT NULL AND ($1::text IS NULL OR o.reference=$1)
            ORDER BY o.paid_at LIMIT $2""", reference, limit)
    done = 0
    for o in todo:
        try:
            async with AsyncSessionLocal() as db:
                await leads.record_payment(db, o["crm_lead_id"], amount=float(o["amount"]), paid_on=o["paid_at"].date().isoformat(),
                                           learner_no=o["learner_no"], invoice_numbers=list(o["invoices"] or []), method=o["method"])
            async with transaction(write=True) as c:
                await c.execute("UPDATE web_orders SET crm_synced_at=now() WHERE id=$1", o["id"])
            done += 1
        except Exception:
            logger.exception("CRM update for website order %s failed; will retry", o["reference"])
    return done
