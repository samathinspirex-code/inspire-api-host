"""Fee figures read from the Payments ledger, so dashboards never count gateway records separately.

A paid invoice counts as on time when its last payment arrived by its due date.
Success-page redirects never reach these tables; only recorded payments do.
"""
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


def _money(value: float) -> str:
    return f"LKR {value / 1e6:.1f}M" if value >= 1e6 else f"LKR {value:,.0f}"


def summarize(row: dict | None, mode: str) -> dict:
    if not row or row.get("demo_data"):
        return {"live": False, "mode": mode}
    due = int(row["due_invoices"] or 0)
    outstanding = float(row["outstanding"] or 0)
    return {
        "live": True,
        "mode": mode,
        "invoices": int(row["invoices"] or 0),
        "payments": int(row["payments"] or 0),
        "open_invoices": int(row["open_invoices"] or 0),
        "outstanding": outstanding,
        "outstanding_label": _money(outstanding),
        "overdue_7d": int(row["overdue_7d"] or 0),
        "overdue_amount": float(row["overdue_amount"] or 0),
        "due_invoices": due,
        "on_time_rate": round(int(row["paid_on_time"] or 0) / due * 100, 1) if due else None,
        "collected_30d": float(row["collected_30d"] or 0),
    }


async def load_finance(db: AsyncSession) -> dict:
    today = date.today()
    try:
        row = (await db.execute(text("""
            SELECT
              (SELECT demo_data FROM payments.module_state LIMIT 1) AS demo_data,
              (SELECT count(*) FROM payments.invoices WHERE status <> 'cancelled') AS invoices,
              (SELECT count(*) FROM payments.payments) AS payments,
              (SELECT count(*) FROM payments.invoices WHERE status = 'open') AS open_invoices,
              (SELECT coalesce(sum(amount - amount_paid), 0) FROM payments.invoices WHERE status = 'open') AS outstanding,
              (SELECT count(*) FROM payments.invoices WHERE status = 'open' AND due_date < :week_ago) AS overdue_7d,
              (SELECT coalesce(sum(amount - amount_paid), 0) FROM payments.invoices WHERE status = 'open' AND due_date < :today) AS overdue_amount,
              (SELECT count(*) FROM payments.invoices WHERE status IN ('open', 'paid') AND due_date <= :today) AS due_invoices,
              (SELECT count(*) FROM payments.invoices i WHERE i.status = 'paid' AND i.due_date <= :today
                 AND (SELECT max(p.received_date) FROM payments.payments p WHERE p.invoice_id = i.id) <= i.due_date) AS paid_on_time,
              (SELECT coalesce(sum(amount), 0) FROM payments.payments WHERE received_date >= :month_ago) AS collected_30d
        """), {"today": today, "week_ago": today - timedelta(days=7), "month_ago": today - timedelta(days=30)})).mappings().first()
    except DBAPIError:
        # Environments without the Payments schema keep their figures labelled demo.
        await db.rollback()
        row = None
    return summarize(dict(row) if row else None, settings.PAYMENTS_PAYHERE_MODE)


def context_line(finance: dict) -> str:
    if not finance["live"]:
        return "Payments ledger not available: finance figures are demo."
    rate = f"{finance['on_time_rate']}% of due invoices paid on time" if finance["on_time_rate"] is not None else "no invoices due yet"
    return (f"PAYMENTS (live ledger, PayHere {finance['mode']} mode): {finance['outstanding_label']} outstanding on {finance['open_invoices']} open invoices, "
            f"{finance['overdue_7d']} overdue more than 7 days, {rate}, LKR {finance['collected_30d']:,.0f} collected in 30 days, "
            f"{finance['invoices']} invoices in total. Zero means nothing has been recorded yet, not that fees are fully collected.")
