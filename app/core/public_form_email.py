import html
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.mailjet_smtp import send_mailjet_smtp_message


logger = logging.getLogger(__name__)
MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"


@dataclass(frozen=True)
class FormEmailResult:
    sent: bool
    error: str | None = None


def detail_table(rows: list[tuple[str, str | None]]) -> str:
    return "".join(
        "<tr>"
        f'<th style="padding:10px 14px;text-align:left;vertical-align:top;border-bottom:1px solid #e8dff0;color:#5c5266;width:180px">{html.escape(label)}</th>'
        f'<td style="padding:10px 14px;border-bottom:1px solid #e8dff0;color:#21162d">{html.escape(value or "—")}</td>'
        "</tr>"
        for label, value in rows
    )


def form_email_html(title: str, intro: str, rows: list[tuple[str, str | None]], message: str | None = None) -> str:
    message_block = ""
    if message:
        message_block = (
            '<div style="margin-top:22px;padding:18px;border-radius:10px;background:#f6f0fb">'
            '<strong style="display:block;margin-bottom:8px;color:#4b007d">Message</strong>'
            f'<div style="white-space:pre-wrap;color:#21162d">{html.escape(message)}</div></div>'
        )
    return (
        '<div style="font-family:Arial,sans-serif;line-height:1.55;color:#21162d;max-width:680px;margin:auto">'
        '<div style="padding:24px 28px;border-radius:14px 14px 0 0;background:#4b007d;color:#fff">'
        f'<div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.8">Inspire College website</div><h1 style="margin:7px 0 0;font-size:25px">{html.escape(title)}</h1></div>'
        '<div style="padding:26px 28px;border:1px solid #e4d9ec;border-top:0;border-radius:0 0 14px 14px;background:#fff">'
        f'<p style="margin:0 0 20px;color:#5c5266">{html.escape(intro)}</p>'
        f'<table role="presentation" style="width:100%;border-collapse:collapse;border:1px solid #e8dff0;border-radius:10px">{detail_table(rows)}</table>'
        f'{message_block}<p style="margin:22px 0 0;color:#766d7d;font-size:12px">This notification was generated automatically by the Inspire College website.</p></div></div>'
    )


async def send_public_form_email(subject: str, text_body: str, html_body: str, reply_to: str | None = None, custom_id: str = "website-form", attachments: list[dict[str, str]] | None = None, to_email: str | None = None, to_name: str = "Inspire Admissions", cc_email: str | None = None, from_name: str | None = None) -> FormEmailResult:
    if not settings.MAILJET_API_KEY.strip() or not settings.MAILJET_SECRET_KEY.strip() or not settings.PUBLIC_FORM_FROM_EMAIL.strip():
        return FormEmailResult(False, "Mailjet is not configured")
    recipient = (to_email or settings.PUBLIC_FORM_RECIPIENT_EMAIL).strip()
    if not recipient:
        return FormEmailResult(False, "The form recipient is not configured")
    recipients = [{"Email": r.strip(), "Name": to_name} for r in recipient.split(",") if r.strip()]
    if not recipients:
        return FormEmailResult(False, "No valid form recipients configured")
    message = {
        "From": {"Email": settings.PUBLIC_FORM_FROM_EMAIL, "Name": from_name or settings.PUBLIC_FORM_FROM_NAME},
        "To": recipients,
        "Subject": subject,
        "TextPart": text_body,
        "HTMLPart": html_body,
        "CustomID": custom_id[:100],
        "TrackOpens": "disabled",
        "TrackClicks": "disabled",
    }
    if reply_to:
        message["ReplyTo"] = {"Email": reply_to}
    if cc_email:
        message["Cc"] = [{"Email": cc_email, "Name": "Inspire College Enrolments"}]
    if attachments:
        message["Attachments"] = attachments
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                MAILJET_SEND_URL,
                json={"Messages": [message]},
                auth=httpx.BasicAuth(settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY),
            )
        if response.is_error:
            if response.status_code == 429 or response.status_code >= 500:
                smtp_result = await send_mailjet_smtp_message(message)
                if smtp_result.sent:
                    return FormEmailResult(True)
            logger.warning("Mailjet rejected a public form notification with status %s", response.status_code)
            return FormEmailResult(False, "The email provider rejected the message")
        payload = response.json()
        if str(((payload.get("Messages") or [{}])[0]).get("Status", "")).lower() != "success":
            return FormEmailResult(False, "The email provider did not accept the message")
        return FormEmailResult(True)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Public form email failed: %s", type(exc).__name__)
        smtp_result = await send_mailjet_smtp_message(message)
        return FormEmailResult(
            smtp_result.sent,
            None if smtp_result.sent else "The email provider is temporarily unavailable",
        )


def branded_email(body_html: str, footer: str) -> str:
    """The purple Inspire College layout used for emails sent to students."""
    return (
        '<!doctype html><html><body style="margin:0;padding:24px 12px;background:#f5f3f8;font-family:Arial,sans-serif;color:#1a0840">'
        '<table role="presentation" style="width:100%;max-width:600px;margin:auto;border-spacing:0;background:#fff;border:1px solid #e8dff0;border-radius:16px;overflow:hidden">'
        '<tr><td style="padding:30px 28px;background:#3f007c;color:#fff">'
        '<div style="font-size:24px;font-weight:bold">inspire<span style="font-weight:normal"> college</span></div>'
        '<div style="margin-top:10px;font-size:12px;letter-spacing:2px">YOUR NEXT CHAPTER STARTS HERE</div></td></tr>'
        f'<tr><td style="padding:32px 28px;line-height:1.7">{body_html}</td></tr>'
        '<tr><td style="padding:20px 28px;border-top:1px solid #e8dff0;font-size:12px;color:#766d7d;line-height:1.6">'
        f'{html.escape(footer)}</td></tr>'
        '</table></body></html>'
    )


def confirmation_email_content(name: str, *, admission: bool) -> tuple[str, str, str]:
    title = "Your application is received" if admission else "Thank you for getting in touch"
    received = "We have received your admission application." if admission else "We have received your message."
    next_step = "A counsellor will contact you shortly to discuss your interests, answer your questions and guide you through the next steps."
    text = f"Hi {name},\n\n{received}\n\n{next_step}\n\nWe look forward to helping you take your next step with Inspire College.\n\nWarm regards,\nThe Inspire College Admissions Team"
    body = (
        f'<h1 style="margin:0 0 24px;font-size:26px;line-height:1.3">{title}</h1>'
        f'<p>Hi {html.escape(name)},</p><p>{received}</p>'
        f'<div style="padding:20px;margin:24px 0;border-left:4px solid #7b3ec8;background:#f6f0fb"><strong>What happens next?</strong><p style="margin:8px 0 0">{next_step}</p></div>'
        '<p>We look forward to helping you take your next step with Inspire College.</p>'
        '<p style="margin-bottom:0">Warm regards,<br><strong>The Inspire College Admissions Team</strong></p>'
    )
    markup = branded_email(body, "This is an automatic confirmation of your website submission. Our team will be in touch shortly.")
    return f"{title} | Inspire College", text, markup


OFFER_FROM_NAME = "Inspire College Admissions"
OFFER_EMAIL_SUBJECT = "Congratulations! Your Conditional Offer of Admission – Inspire College"
OFFER_REQUIRED_DOCUMENTS = (
    # (document type, name, note, required to fulfil the offer)
    ("nic_passport", "NIC or Passport", "A clear copy of your National Identity Card or Passport.", True),
    ("qualification", "Highest Educational Qualification", "Certificate or results sheet.", True),
    ("transcript", "Academic Transcript", "If applicable.", False),
)
_OFFER_INTRO = (
    "Your application has been successfully received and reviewed based on the information provided in your admission form. "
    "We are delighted to welcome you to the next stage of your journey with Inspire College."
)
_OFFER_NEXT_STEPS = (
    "To secure your place, please complete the remaining admission requirements, including submission and verification of the "
    "required supporting documents and completion of the applicable registration/payment requirements."
)
_OFFER_NEXT_STEPS_RECEIVED = (
    "We have received your supporting documents, and our Admissions Team is now verifying them. To secure your place, please "
    "complete the applicable registration/payment requirements."
)
_OFFER_DOCUMENTS_RECEIVED = "Thank you, we have already received your documents. You do not need to submit them again."
_OFFER_CLOSING = (
    "Once your documents have been submitted and verified, and the programme entry requirements have been met, our Admissions Team will guide you through the final enrolment process.",
    "Once all admission conditions have been successfully fulfilled, your admission will be confirmed, and you will receive the relevant enrolment and programme commencement information.",
    "Please note that this offer remains conditional until all admission requirements have been satisfied and verified by Inspire College.",
    "We are excited about the opportunity to have you join the Inspire College community and look forward to supporting you throughout your academic journey.",
    "Once again, congratulations on receiving your Conditional Offer!",
)


def offer_letter_email_content(first_name: str, full_name: str, programme: str, link: str, expiry: str, received: frozenset[str] | set[str] = frozenset()) -> tuple[str, str, str]:
    """The Conditional Offer of Admission email.

    Documents in `received` (document types already uploaded) are not asked for again.
    When every required document is in, the upload request is left out entirely.
    """
    details = (("Student Name", full_name), ("Programme", programme), ("Study Mode", "Online"), ("Offer Status", "Conditional Offer"))
    outstanding = [(name, note) for doc_type, name, note, _required in OFFER_REQUIRED_DOCUMENTS if doc_type not in received]
    received_names = [name for doc_type, name, _note, _required in OFFER_REQUIRED_DOCUMENTS if doc_type in received]
    all_required_in = all(doc_type in received for doc_type, _name, _note, required in OFFER_REQUIRED_DOCUMENTS if required)
    next_steps = _OFFER_NEXT_STEPS_RECEIVED if all_required_in else _OFFER_NEXT_STEPS
    already = f"We have already received: {', '.join(received_names)}." if received_names else ""

    if all_required_in:
        doc_text = ["DOCUMENTS", "", _OFFER_DOCUMENTS_RECEIVED, ""]
    else:
        doc_text = [
            "REQUIRED DOCUMENTS & SUBMISSION", "",
            "To fulfil the conditions of your offer and proceed with admission, please submit the following documents:", "",
            *(f"- {name} – {note}" for name, note in outstanding), "",
            *([already, ""] if already else []),
            f"Submit Your Documents: {link}", "",
        ]
    text = "\n".join([
        f"Dear {first_name},", "", f"Congratulations, {first_name}!", "",
        "We are pleased to offer you a Conditional Offer of Admission to study at Inspire College.", "",
        "YOUR OFFER DETAILS", "", *(f"{label}: {value}" for label, value in details), "",
        _OFFER_INTRO, "", "YOUR NEXT STEPS", "", next_steps, "",
        *doc_text, f"Offer Valid Until: {expiry}", "",
        *[line for paragraph in _OFFER_CLOSING for line in (paragraph, "")],
        "Warm regards,", "Admissions Team", "Inspire College", "076 097 4225",
    ])

    # Plain personal-letter HTML: no banner, coloured boxes, images or buttons.
    # Heavily designed layouts are what Gmail files under Promotions.
    e = html.escape

    def para(value: str) -> str:
        return f"<p>{e(value)}</p>"

    def heading(value: str) -> str:
        return f"<p><b>{e(value)}</b></p>"

    lines = [
        para(f"Dear {first_name},"),
        para(f"Congratulations, {first_name}!"),
        para("We are pleased to offer you a Conditional Offer of Admission to study at Inspire College."),
        heading("Your offer details"),
        "<p>" + "<br>".join(f"{e(label)}: {e(value)}" for label, value in details) + "</p>",
        para(_OFFER_INTRO),
        heading("Your next steps"),
        para(next_steps),
    ]
    if all_required_in:
        lines += [heading("Documents"), para(_OFFER_DOCUMENTS_RECEIVED)]
    else:
        lines += [
            heading("Required documents & submission"),
            para("To fulfil the conditions of your offer and proceed with admission, please submit the following documents:"),
            "<ul>" + "".join(f"<li>{e(name)} – {e(note)}</li>" for name, note in outstanding) + "</ul>",
        ]
        if already:
            lines.append(para(already))
        lines.append(f'<p>Submit your documents: <a href="{e(link)}">{e(link)}</a></p>')
    lines.append(f"<p>Offer valid until: {e(expiry)}</p>")
    lines += [para(paragraph) for paragraph in _OFFER_CLOSING]
    lines.append("<p>Warm regards,<br>Admissions Team<br>Inspire College<br>076 097 4225</p>")
    markup = (
        '<!doctype html><html><body><div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5;color:#222">'
        + "".join(lines)
        + "</div></body></html>"
    )
    return OFFER_EMAIL_SUBJECT, text, markup
