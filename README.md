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
8. Bulk student invitations and an invitation audit history — next onboarding
   improvement.
9. Central notification system — planned for a later phase. Add PostgreSQL-backed
   notification jobs, a background worker, retry and delivery tracking, in-app
   notifications, user preferences, and scheduled reminders for assignments,
   meetings, attendance and announcements. Use the current Mailjet delivery
   adapter while keeping the provider replaceable if production volume later
   makes Amazon SES or another provider more economical.
