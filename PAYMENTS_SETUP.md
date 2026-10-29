# Payments setup and operating guide

The entire Accounts workspace now has a Python backend in `app/modules/payments`.
It runs in the existing FastAPI application, uses the existing PostgreSQL connection
settings and CMS login, and stores financial records in the **payments** schema.
It needs Python and PostgreSQL 14+; no Docker, Node worker, extra login, or separate
public backend is required. The original `inspire-brain-accounts` reference remains intact.

## 1. Start with the existing dummy data

From the `inspire-api-host` directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\apply_payments_migration.py --demo
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
```

The migration creates only the accounting schema. `--demo` imports the original
134 learners, 939 journals, invoices, instalments, payment links, suppliers, bills,
assets, closed periods and bank lines. It also reconstructs receipt records from
the sample journals and checks that the trial balance, balance sheet and cash
flow reconcile before committing. Sample records retain their reference dates
(snapshot: 19 October 2026); the connected dashboard uses today's Colombo date.

Running the command again preserves changes already made to the sample ledger.
The importer refuses to mix sample data with an existing real ledger. Without
`--demo`, it creates an empty module with the chart and reference programmes only.
The script uses the database configured in `.env`; choose the intended environment
before running it. The migration changes that selected database; the optional
verification exercise below rolls back its temporary workflow records.

From `CMS-UI`, use the existing frontend setup. Its `VITE_API_BASE_URL` must point
to this backend's `/api/v1/cms` address, for example:

```dotenv
VITE_API_BASE_URL=http://localhost:8001/api/v1/cms
```

Restart the CMS development server after changing that variable, then open Payments.

| View | Data and behaviour |
|---|---|
| Connected ledger | Saves changes in PostgreSQL. With `--demo`, it says **Saved sample ledger** and offers **Simulate payment**. |
| Original demo | Preserves the original local sample view. Changes disappear when the view is closed. **Reset demo** affects this view only. |

The API does not silently fall back to demo data. A setup error means the schema
has not been migrated on the backend selected by the CMS.

## 2. Access

Every private Payments endpoint requires the existing **FINANCE** access grant.
ADMIN or SUPER_ADMIN on its own does not grant access to financial data.

FINANCE permits ordinary accounting operations: learners, billing, receipts,
links, banking, bills, journals, reports, closing runs and locking periods.
FINANCE **plus** ADMIN or SUPER_ADMIN is required to change policies, maintain
accounts/programmes, reopen periods, inspect the audit log and run year-end.
Assign these through existing User Management; there is no second set of users.
The pre-existing `apply_payments_access_migration.py` changes account grants;
it is separate from this schema migration and is not required to create tables.

The iframe keeps its restricted browser permissions. API calls pass through the
CMS parent, using its normal access-token refresh mechanism. Login tokens and
gateway secrets are not embedded in the iframe.

## 3. Try the connected workspace

1. Open **Learners & fees**, add a test learner with email and phone, choose a
   programme, and enter a total fee and instalment count. Learner creation and
   enrolment are one transaction, so an invalid fee schedule leaves no orphan learner.
2. Open **Payment links**, select that learner and create a link. This invoices
   the next instalment and queues its message. Registration links reuse the open
   registration invoice created during enrolment.
3. While the saved dummy ledger is loaded, choose **Simulate payment**. It creates
   a sample receipt and balanced journal, and updates the invoice and instalment.
   No gateway or message provider is contacted.
4. Alternatively, record a cash or bank-transfer receipt from the learner panel.
   Partial receipts reduce the invoice balance; excess receipts are learner advances.
   If a partial receipt changes an existing link's amount, cancel that link and
   create a replacement before checkout.
5. Import bank CSV with `date,description,amount,reference` columns. Positive
   amounts are deposits; negative amounts are withdrawals. Dates accept YYYY-MM-DD
   or DD/MM/YYYY. Reimported identical lines are ignored. Match a suggested journal,
   post a bank charge, or settle a PayHere payout with its actual fee.
6. Add a supplier and bill, then pay it. Review the journals and reports.
7. In **Close**, preview and post revenue recognition, depreciation and ECL.
   Repeating an unchanged run posts no additional journal. Complete the checklist
   before locking the month. An authorised administrator can reopen it with a reason.

The original April–September sample periods remain locked. Use an open month
for new examples. Only manual/opening-balance journals offer generic reversals;
service-generated entries must be corrected through their owning workflow so
invoice, payment, bank and close records remain consistent.

## 4. PayHere sandbox and later live payments

Configure these **server-side** values in the API `.env`, then restart it:

```dotenv
PAYMENTS_PUBLIC_BASE_URL=https://your-public-api-domain.example
PAYMENTS_PAYHERE_MODE=sandbox
PAYMENTS_PAYHERE_MERCHANT_ID=your-sandbox-merchant-id
PAYMENTS_PAYHERE_MERCHANT_SECRET=your-domain-merchant-secret
PAYMENTS_MESSAGES_ENABLED=false
```

`PAYMENTS_PUBLIC_BASE_URL` is the backend origin, without `/api` or a trailing path.
The backend serves the learner page at `/pay/<token>`. Keep its public routes
reachable through your reverse proxy:

| Purpose | Route |
|---|---|
| Learner page | `GET /pay/<token>` |
| Link details | `GET /api/v1/payments/public/pay/<token>` |
| Checkout | `POST /api/v1/payments/public/pay/<token>/checkout` |
| PayHere notification | `POST /api/v1/payments/payhere/notify` |

Use the domain-specific secret from the PayHere Integrations page. PayHere requires
a publicly reachable notification address; it cannot call localhost. The checkout
hash and callback verification follow the [official Checkout API documentation](https://support.payhere.lk/api-&-mobile-sdk/checkout-api).

With sandbox credentials, create a fresh sample link and open it in a browser.
The server saves a distinct order for each checkout attempt. Only a signed,
successful LKR notification for that exact order and amount can post a receipt.
The browser return URL is never proof of payment. Replayed callbacks post nothing.

Live checkout is blocked while `demo_data=true`. Automatic billing and message
delivery are also blocked with sample data, regardless of provider credentials.
There is deliberately no setting in the UI that can remove these protections.

## 5. Messages and daily jobs

Creating a link **queues** its message; it does not mean it was delivered.
Queued messages remain queued while delivery is disabled. Review records using
`GET /api/v1/cms/payments/messages`; five failed delivery attempts mark a message failed.

After moving to real data, enable `PAYMENTS_MESSAGES_ENABLED=true` and configure
the channels you use:

- Email uses the existing Mailjet integration and `PUBLIC_FORM_FROM_EMAIL` /
  `PUBLIC_FORM_FROM_NAME` sender settings.
- WhatsApp uses the `PAYMENTS_WHATSAPP_*` settings, a supported Graph API version,
  and an approved template with name, purpose, amount, due date and link parameters.
- SMS uses the HTTPS `PAYMENTS_SMS_URL` adapter, with JSON fields `sender_id`,
  `to` and `message`. Match this adapter to the actual provider before enabling it.

Schedule these commands externally using Task Scheduler, cron or a service timer:

```powershell
# Every day at 07:00 Asia/Colombo:
.\.venv\Scripts\python.exe scripts\run_payments_worker.py --daily

# Every minute, once delivery has been enabled:
.\.venv\Scripts\python.exe scripts\run_payments_worker.py --send
```

Use the API repository as the working directory so `.env` is found. Daily billing
invoices upcoming instalments, applies configured late fees, queues reminders and
expires old links. Late fees default to **disabled** until their policy is confirmed.
Database locks make overlapping worker invocations safe. Message delivery is
at least once: a process crash after provider acceptance but before saving the
result can lead to a retried message. Monitor and reconcile provider delivery records.

## 6. API and operational details

Private routes use `/api/v1/cms/payments`, including accounts, journals, programmes,
learners, enrolments, invoices, receipts, payment-links, banking, suppliers, bills,
fixed-assets, reports, close, settings, audit, gateway-events and messages.
See the existing API's `/docs` for request models and paths.

`POST /learner-enrolments` atomically creates a learner and enrolment. An authenticated
`POST /integrations/lms/enrolment` accepts the same payload plus `lms_ref` and reuses
that learner identifier. It is an explicit integration endpoint; existing LMS/CRM
enrolment events are not automatically wired to financial billing.

Financial create endpoints require an **Idempotency-Key** header. Reuse the same
key when retrying the same request after a lost response. Reusing a key with a
different payload is rejected. The CMS supplies keys and preserves them during
session-refresh retries. Refresh the workspace to verify a timed-out action
before manually submitting it again.

Money uses Python Decimal and PostgreSQL NUMERIC. Every mutation uses a database
transaction, and private responses are sent after commit. The database rejects
unbalanced or empty journals, edits/deletes of posted journals, appended lines
after commit, duplicate gateway receipts, duplicate reversals, duplicate bank
matches, and journal posting into locked periods. Financial writes are serialised
within this module to protect balances and closing operations.

Ageing reports use receipts/payments through the requested date, rather than
today's paid status. Statement dates use Asia/Colombo. CSV exports neutralise
spreadsheet formulas in text fields.

Public routes have bounded per-process rate limits. Configure shared reverse-proxy
limits when running multiple API processes. The Payments database pool adds up
to `PAYMENTS_DATABASE_POOL_SIZE` connections per process (default 3), in addition
to the existing API pool. Include that in your hosting connection budget.

## 7. When ready to remove the dummy data

The CMS tour uses its own in-memory sample snapshot. Finishing or skipping the
tour restores the connected ledger and never imports sample records. The
configured development practice ledger was archived locally before cutover;
the normal Payments screens now show the connected ledger's own records only.

If another environment has a saved sample ledger, use it as a practice
environment. After taking a backup,
an administrator with the explicit FINANCE grant can select **Start with blank
Payments data** in Settings. The confirmation dialog requires typing
`CLEAR SAMPLE DATA`. The API's `POST /api/v1/cms/payments/sample-data/clear`
transaction removes all sample and practice financial records, settings, audit
history and queued messages inside the `payments` schema. It keeps the chart of
accounts and programme templates, marks `demo_data=false`, and writes a cutover
audit entry. The sample import is permanently retired in this schema, so
re-running the migration with `--demo` cannot restore it. Existing CMS/LMS tables
are separate and untouched. The UI never clears this data automatically.

Before accepting real money, set approved programme fees, teaching months,
registration-fee policy, late-fee policy, ECL rates, fiscal year and opening
balances; then complete a PayHere sandbox transaction through its public callback.
Switch the fresh production module to `PAYMENTS_PAYHERE_MODE=live` with live,
domain-specific credentials and HTTPS. Enable messages only after delivery tests.

Gateway rejections are retained at `/gateway-events`, with card details excluded.
If a valid capture arrives after cancellation/expiry, after the invoice balance
changed, or in a locked period, it is logged for manual investigation rather than
silently changing the ledger. A chargeback notification is retained for review;
this module does not automatically issue refunds or reconcile gateway disputes.

## 8. Verification

Check the configured database and exercise a temporary workflow after loading
dummy data:

```powershell
.\.venv\Scripts\python.exe scripts\verify_payments.py --exercise
```

This checks every report, sample billing and receipts, bill payments, journal
reversal, close previews and database constraints. It rolls back the workflow
and verifies that the saved record counts are unchanged. Without `--exercise`,
the command checks reports and counts only.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_payments tests.test_live_account_access
```

The full integration test requires an explicitly selected **disposable local**
PostgreSQL database. It will create the payments schema and load the sample ledger:

```powershell
$env:PAYMENTS_TEST_DATABASE_URL='postgresql://postgres:password@127.0.0.1:5432/payments_test'
.\.venv\Scripts\python.exe -m unittest tests.test_payments_integration
```

The workflow tests roll back their changes after verifying the API, callback
idempotency, bank import/matching, closing, ledger constraints and access rules.
The seeded reference ledger remains in that disposable database.
