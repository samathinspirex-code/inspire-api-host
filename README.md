# inspire-api
backend for cms and LMS

## News & Events

Before using the CMS News & Events section, create its table and import the existing website news records:

```powershell
.\.venv\Scripts\python.exe scripts\apply_news_events_migration.py
```

See [NEWS_EVENTS_API_DOCUMENTATION.md](NEWS_EVENTS_API_DOCUMENTATION.md) for endpoints and sample payloads.

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate (.venv\Scripts\Activate.ps1)
pip install -r requirements.txt
```

Copy the env template and fill in your Postgres credentials:

```bash
cp .env.example .env
```

Generate and save a stable Authenticator encryption key in `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run the Authenticator migration before starting the updated API. This creates
Authenticator credentials, setup tokens and recovery codes, and removes the
retired email OTP table:

```bash
python scripts/apply_authenticator_migration.py
```

Alternatively, run `app/modules/auth/sql/authenticator.sql` in the Supabase SQL
Editor.

For the first administrator only, generate a single-use setup token from the
API folder:

```bash
python scripts/create_authenticator_setup_token.py admin@example.com
```

After that administrator signs in, all other setup/reset tokens can be created
and emailed from **CMS > User Management > Authenticator**. Configure the
Mailjet API/secret keys and a validated `MAILJET_FROM_EMAIL` sender first. Add
SPF and DKIM records for the sender domain before production use. If delivery
fails, the UI provides the same single-use setup link for manual sharing.

Authenticator setup invitations expire after **2 days (48 hours)** by default.
Set `AUTHENTICATOR_SETUP_EXPIRE_MINUTES=2880` in the deployed API environment
if it already overrides this setting, then restart the API. This applies to
newly issued invitations only; existing links keep their original expiry.
Resend an invitation to issue a new link and invalidate the previous unused link.
Authenticator sign-in codes and login session lifetimes are unchanged.

Creating a new user in the CMS UI now sends their setup invitation automatically;
editing a user does not send/reset an invitation. Users with both CMS and LMS
access receive separately labelled links for both portals in one email, using
`CMS_UI_URL` and `LMS_UI_URL`. Configure these to the deployed frontend addresses.
Both setup links share one single-use invitation: complete setup once, then use
the same Authenticator to sign in to either portal. The email also includes the
regular portal sign-in addresses for use after setup. If email delivery fails,
the CMS shows both setup links for manual sharing without recreating the user.

## Run

```bash
uvicorn app.main:app --reload
```

The API will be available at http://127.0.0.1:8000, with docs at http://127.0.0.1:8000/docs and a health check at http://127.0.0.1:8000/health.

## LMS database scripts

Run the LMS SQL scripts against the existing Inspire PostgreSQL database in this order:

1. `seed_roles.sql`
2. `academic_core.sql`
3. `course_modules.sql`
4. `course_classes.sql`
5. `people_profiles.sql`
6. `assignments_enrolments.sql`
7. `google_integration.sql`
8. `online_meetings.sql`
9. `attendance.sql`
10. `sso_handoff.sql`
11. `course_content_studio.sql`
12. `learning_progress.sql`

The scripts are under `app/modules/lms/sql`. They are idempotent and can be run through the Supabase SQL Editor. Existing installations adding automatic attendance need to run only `attendance.sql` after pulling this version.

## CMS media storage

The Media Library uses S3-compatible object storage. Configure the `MEDIA_*`
environment values, install the updated requirements, then run:

```powershell
.\.venv\Scripts\python.exe scripts\apply_media_storage_migration.py
```

On AWS, prefer an IAM role limited to the selected bucket rather than storing
permanent AWS access keys in `.env`.

Google Meet attendance identity matching also requires the **Google People API** to be enabled in the same Google Cloud project as the Meet and Calendar APIs.

For existing Course Studio installations, apply student learning progress with:

```powershell
.\.venv\Scripts\python.exe scripts\apply_learning_progress_migration.py
```

Enable the course knowledge assistant tables before configuring chatbots in the CMS:

```powershell
.\.venv\Scripts\python.exe scripts\apply_course_assistant_migration.py
```

The migration is idempotent and also upgrades an existing course-assistant
installation with page-aware knowledge chunks. In the CMS, open **Bots → Bot
Overview → Configure assistant**, then select **Sync course content**. This
indexes Course Studio text lessons and downloads/extracts PDF learning items.
Scanned image-only PDFs require a later OCR step.

Retrieval and extractive summaries work without an external AI provider. To
turn retrieved passages into natural grounded explanations, configure
`OPENAI_API_KEY` and optionally `OPENAI_MODEL` in the API environment. The key
must stay server-side and must never be exposed through a `VITE_` variable.

## LMS implementation order

1. Academic structure: programmes, courses, modules and classes — completed.
2. Student enrolment and lecturer assignment — completed.
3. Google account connection, online meetings and calendar — completed.
4. Automatic Google Meet attendance with lecturer review — completed.
5. Google Authenticator sign-in and recovery codes — completed.
6. Automated Authenticator setup invitations — completed. Creating a student
   or lecturer from the LMS emails a single-use setup link. Admins can resend,
   replace expired invitations, or reset a configured Authenticator. The CMS
   User Management action uses the same email flow. Setup links open the
   correct UI with the email and token loaded and retain a manual copy-link
   fallback when delivery fails.
7. Per-item student learning progress, Vimeo watch-time reporting and lecturer
   progress visibility — completed foundation. Quiz attempts and automatic
   progression rules are the next Course Studio phase.
8. Bulk student CSV import with optional invitations — implemented. An invitation
   audit history and bulk invitations for existing accounts remain future work.
9. Central notification system — planned for a later phase. Add PostgreSQL-backed
   notification jobs, a background worker, retry and delivery tracking, in-app
   notifications, user preferences, and scheduled reminders for assignments,
   meetings, attendance and announcements. Use the current Mailjet delivery
   adapter while keeping the provider replaceable if production volume later
   makes Amazon SES or another provider more economical.

### Bulk student CSV import

LMS admins and super admins can open **Students → Import Excel / CSV** to download
the template, upload `.xlsx` or UTF-8 CSV, or paste CSV text. In Excel, open the
template, replace its example row, and use **Save As → Excel Workbook (.xlsx)**.
Keep phone and student numbers as Text to preserve leading zeros.

- Required columns: `full_name,email,student_number`. Optional: `phone,notes`.
- Maximum 100 students per batch. CSV is limited to 500 KB; Excel to 2 MB.
- Excel reads only the first, visible worksheet. Put headers in row 1 and data in
  rows 2–101, using only columns A–E. Merged cells, formulas, errors, macros,
  encrypted/protected files and legacy `.xls` are rejected. Save `.xls` or `.xlsm`
  as a plain `.xlsx` first. Other worksheets are ignored and reported in the UI.
- Preview validates required fields, email format, column lengths, duplicate rows,
  and existing emails/student numbers (including accounts outside the LMS).
- Correct every row before confirming. Confirmation rechecks the database and
  creates the entire batch in one transaction. Existing accounts are never updated.
- New accounts receive only active `LMS` and `STUDENT` access. No automatic course
  or class enrolments are made. Profile photos can be uploaded after import.
- Optional setup emails use the existing invitation flow and expiry setting
  (48 hours by default). The browser sends at most two invitations concurrently
  after account creation. Keep the import dialog open until it finishes.
- Email errors do not undo accounts. Check each result and use the existing
  **Send invitation** action as needed. Never re-import just to retry an email.
  If the browser closes mid-send, inspect invitation statuses in Students before
  resending; emails are not queued in a durable background job.

API: `POST /api/v1/lms/students/import` accepts `csv_text` and `preview`
(`true` by default). Preview does not write. Use `preview: false` after review;
validation failures return row errors with `imported: 0`. Both operations require
an LMS admin role. `POST /api/v1/lms/students/import/excel?preview=true|false`
accepts the raw `.xlsx` body with the standard Excel MIME type and uses the same
atomic importer. Responses are not cached. No database migration is required.
