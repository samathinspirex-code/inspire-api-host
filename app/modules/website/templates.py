"""Branded website payment emails. Each returns (subject, html, text). Links point at the new website's /pay/<token>."""
from datetime import date
from html import escape

from app.core.config import settings

BRAND = "#5b21b6"


def lkr(n) -> str:
    return f"LKR {float(n or 0):,.2f}".replace(".00", "")


def pay_url(token: str) -> str:
    return f"{settings.WEB_SITE_URL.rstrip('/')}/pay/{token}"


def fmt_date(d) -> str:
    if not d:
        return ""
    try:
        return date.fromisoformat(str(d)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(d)


def _layout(title: str, body_html: str, cta: tuple[str, str] | None = None, foot: str = "") -> str:
    button = ""
    if cta:
        button = (f'<p style="margin:26px 0"><a href="{escape(cta[1])}" style="background:{BRAND};color:#fff;text-decoration:none;'
                  f'padding:13px 22px;border-radius:999px;font-weight:600;display:inline-block">{escape(cta[0])}</a></p>'
                  f'<p style="font-size:12px;color:#6b6280">Or open this link: <a href="{escape(cta[1])}" style="color:{BRAND}">{escape(cta[1])}</a></p>')
    return f"""<!doctype html><html><body style="margin:0;background:#f5f2fb;font-family:Arial,Helvetica,sans-serif;color:#1d1530">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="100%" style="max-width:560px;background:#fff;border-radius:22px;overflow:hidden" cellpadding="0" cellspacing="0">
<tr><td style="background:{BRAND};padding:20px 28px;color:#fff;font-size:20px;font-weight:700">inspire<span style="font-weight:400;opacity:.85">college</span></td></tr>
<tr><td style="padding:28px">
<h1 style="margin:0 0 14px;font-size:22px;line-height:1.3">{escape(title)}</h1>
{body_html}{button}
<p style="margin-top:28px;font-size:13px;color:#6b6280">{escape(foot) if foot else "Questions? Reply to this email or WhatsApp us on +94 76 097 4225."}</p>
</td></tr></table>
<p style="font-size:11px;color:#8a8199;margin-top:14px">Inspire College · Colombo, Sri Lanka</p>
</td></tr></table></body></html>"""


def _rows(pairs) -> str:
    tr = "".join(f'<tr><td style="padding:7px 0;color:#6b6280;width:120px">{escape(k)}</td><td style="padding:7px 0;font-weight:600">{escape(str(v))}</td></tr>'
                 for k, v in pairs if v not in (None, ""))
    return f'<table role="presentation" width="100%" style="border-top:1px solid #eee;border-bottom:1px solid #eee;margin:16px 0">{tr}</table>'


def schedule_html(schedule: list[dict]) -> str:
    if not schedule:
        return ""
    tr = "".join(
        f'<tr><td style="padding:6px 8px">{escape(s["label"])}</td><td style="padding:6px 8px">{escape(fmt_date(s["due_date"]))}</td>'
        f'<td style="padding:6px 8px;text-align:right">{escape(lkr(s["amount"]))}</td>'
        f'<td style="padding:6px 8px;color:{"#15803d" if s["status_label"] == "Paid" else "#6b6280"}">{escape(s["status_label"])}</td></tr>'
        for s in schedule)
    return ('<p style="margin:18px 0 6px;font-weight:600">Your payment schedule</p>'
            f'<table role="presentation" width="100%" style="font-size:13px;border-collapse:collapse;background:#faf8fe;border-radius:12px">{tr}</table>')


def schedule_text(schedule: list[dict]) -> str:
    return "\n".join(f"- {s['label']}: {lkr(s['amount'])}, due {fmt_date(s['due_date'])} ({s['status_label']})" for s in schedule)


def order_created(first: str, ref: str, course: str, amount, token: str, bank: dict) -> tuple[str, str, str]:
    subject = f"Your enrolment {ref} — pay by bank transfer"
    bank_rows = [("Account name", bank.get("account_name")), ("Bank", bank.get("bank")), ("Branch", bank.get("branch")),
                 ("Account no.", bank.get("account_no")), ("Reference", ref)]
    body = (f"<p>Hi {escape(first)},</p><p>Thank you for enrolling in <b>{escape(course)}</b>. Transfer <b>{escape(lkr(amount))}</b> "
            "and upload your slip with the button below. We confirm slips within one working day.</p>" + _rows(bank_rows)
            + (f'<p style="font-size:13px;color:#6b6280">{escape(bank.get("note") or "")}</p>' if bank.get("note") else ""))
    text = (f"Hi {first},\n\nThank you for enrolling in {course}. Transfer {lkr(amount)} and upload your slip here: {pay_url(token)}\n\n"
            + "\n".join(f"{k}: {v}" for k, v in bank_rows if v) + "\n")
    return subject, _layout("Complete your enrolment", body, ("Upload my bank slip", pay_url(token))), text


def invoice_issued(first: str, number: str, purpose: str, course: str, amount, due, token: str) -> tuple[str, str, str]:
    subject = f"Invoice {number} — {purpose} due {fmt_date(due)}"
    body = (f"<p>Hi {escape(first)},</p><p>Your next payment for <b>{escape(course)}</b> is ready.</p>"
            + _rows([("Invoice", number), ("For", purpose), ("Amount", lkr(amount)), ("Due", fmt_date(due))])
            + "<p>Pay securely by card, or upload your bank-transfer slip, using the button below.</p>")
    text = (f"Hi {first},\n\nYour next payment for {course} is ready.\nInvoice: {number}\nFor: {purpose}\nAmount: {lkr(amount)}\n"
            f"Due: {fmt_date(due)}\n\nPay by card or upload your bank slip: {pay_url(token)}\n")
    return subject, _layout("Your invoice is ready", body, ("Pay now", pay_url(token))), text


REMINDER_LINES = {
    1: ("Payment due soon", "is due on {due}."),
    2: ("Payment due today", "is due today."),
    3: ("Payment overdue", "was due on {due} and is now overdue. Please pay as soon as you can to keep your place."),
}


def reminder(first: str, purpose: str, amount, due, token: str, stage: int) -> tuple[str, str, str]:
    title, line = REMINDER_LINES.get(stage, REMINDER_LINES[1])
    line = line.format(due=fmt_date(due))
    subject = f"{title}: {purpose} — {lkr(amount)}"
    body = (f"<p>Hi {escape(first)},</p><p>A friendly reminder that your {escape(purpose.lower())} of <b>{escape(lkr(amount))}</b> {escape(line)}</p>"
            "<p>Pay securely by card, or upload your bank-transfer slip, using the button below.</p>")
    text = f"Hi {first},\n\nA friendly reminder that your {purpose.lower()} of {lkr(amount)} {line}\n\nPay: {pay_url(token)}\n"
    return subject, _layout(title, body, ("Pay now", pay_url(token))), text


def receipt(first: str, amount, method: str, what: str, numbers: list[str], token: str, schedule: list[dict], next_item: dict | None) -> tuple[str, str, str]:
    how = "card (PayHere)" if method == "payhere" else "bank transfer"
    subject = f"Payment received — {what} ({lkr(amount)})"
    nxt = nxt_t = ""
    if next_item:
        nxt = (f"<p>Your next payment is <b>{escape(lkr(next_item['amount']))}</b>, due <b>{escape(fmt_date(next_item['due_date']))}</b>. "
               "We'll email you the invoice a few days before.</p>")
        nxt_t = f"\nNext payment: {lkr(next_item['amount'])}, due {fmt_date(next_item['due_date'])}.\n"
    body = (f"<p>Hi {escape(first)},</p><p>Thank you! We received <b>{escape(lkr(amount))}</b> by {escape(how)} for <b>{escape(what)}</b>"
            f" (invoice {escape(', '.join(numbers))}).</p>" + nxt + schedule_html(schedule))
    text = (f"Hi {first},\n\nThank you! We received {lkr(amount)} by {how} for {what} (invoice {', '.join(numbers)}).\n{nxt_t}"
            + (f"\nYour schedule:\n{schedule_text(schedule)}\n" if schedule else ""))
    return subject, _layout("Payment received", body, ("View my payments", pay_url(token))), text


def slip_received(first: str, ref: str, what: str) -> tuple[str, str, str]:
    subject = f"We received your bank slip — {ref}"
    body = (f"<p>Hi {escape(first)},</p><p>Thanks for uploading your bank slip for <b>{escape(what)}</b>. Our finance team will check it, "
            "usually within one working day, and email you when it's confirmed.</p>")
    text = f"Hi {first},\n\nThanks for uploading your bank slip for {what} ({ref}). Our finance team will check it and email you when it's confirmed.\n"
    return subject, _layout("Slip received", body), text


def slip_rejected(first: str, ref: str, what: str, reason: str, token: str) -> tuple[str, str, str]:
    subject = f"Please check your bank slip — {ref}"
    body = (f"<p>Hi {escape(first)},</p><p>We couldn't confirm the bank slip you uploaded for <b>{escape(what)}</b>.</p>"
            f'<p style="background:#fff4f4;border-radius:12px;padding:12px 14px">{escape(reason)}</p>'
            "<p>You can upload a new slip or pay by card using the button below.</p>")
    text = f"Hi {first},\n\nWe couldn't confirm the bank slip you uploaded for {what} ({ref}).\nReason: {reason}\n\nUpload again or pay by card: {pay_url(token)}\n"
    return subject, _layout("We couldn't confirm your slip", body, ("Upload again or pay by card", pay_url(token))), text


def finance_capture_alert(order_id: str, payment_id: str, amount, reason: str, student: str) -> tuple[str, str, str]:
    subject = f"Card payment needs review — {order_id} ({lkr(amount) if amount is not None else 'amount unknown'})"
    body = (f"<p>PayHere confirmed a card payment, but it was <b>not recorded in Accounts</b>.</p>"
            f"<p>Order: <b>{escape(order_id)}</b><br>PayHere payment ID: <b>{escape(payment_id or '-')}</b><br>"
            f"Amount: <b>{escape(lkr(amount) if amount is not None else '-')}</b><br>Student: {escape(student or '-')}<br>Reason: {escape(reason)}</p>"
            "<p>Check the PayHere dashboard. Either refund the payment in PayHere, or record it on the learner in Payments → Learners & fees.</p>")
    text = (f"PayHere confirmed a card payment that was NOT recorded in Accounts.\nOrder: {order_id}\nPayHere payment ID: {payment_id or '-'}\n"
            f"Amount: {lkr(amount) if amount is not None else '-'}\nStudent: {student or '-'}\nReason: {reason}\n"
            "Refund it in PayHere, or record it on the learner in Payments -> Learners & fees.\n")
    return subject, _layout("Card payment needs review", body, foot="Inspire website · payments"), text


def finance_slip_alert(student: str, ref: str, what: str, amount, reference: str) -> tuple[str, str, str]:
    subject = f"New bank slip to review — {ref} ({lkr(amount)})"
    body = (f"<p>{escape(student)} uploaded a bank slip for <b>{escape(what)}</b>.</p>"
            f"<p>Amount: <b>{escape(lkr(amount))}</b><br>Bank reference: {escape(reference or '-')}</p>"
            "<p>Review it in the CMS: Payments → Bank slips.</p>")
    text = f"{student} uploaded a bank slip for {what} ({ref}).\nAmount: {lkr(amount)}\nBank reference: {reference or '-'}\nReview it in the CMS: Payments -> Bank slips.\n"
    return subject, _layout("Bank slip to review", body, foot="Inspire website · payments"), text
