# Inspire LMS API Documentation

This document describes the LMS and supporting authentication APIs currently implemented in `inspire-api`.

## General information

Development base URL:

```text
http://127.0.0.1:8000/api/v1
```

Interactive FastAPI documentation:

```text
http://127.0.0.1:8000/docs
```

Protected requests require an access token:

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

## LMS roles

| Role | Main access |
|---|---|
| `SUPER_ADMIN` | All current administration APIs |
| `ADMIN` | Programmes, courses, modules, classes, people and assignments |
| `LECTURER` | Assigned courses, modules, classes and students |
| `STUDENT` | Enrolled active courses, active modules, classes and lecturers |

Every LMS user must have the `LMS` permission and one LMS role, for example:

```text
LMS + STUDENT
LMS + LECTURER
LMS + ADMIN
```

## Authentication

### Sign in with Google Authenticator

```http
POST /auth/authenticator/verify
```

Authentication is not required.

Request:

```json
{
  "email": "student@example.com",
  "code": "123456"
}
```

The successful response contains the access token, rotating refresh token and current user:

```json
{
  "access_token": "<jwt-access-token>",
  "refresh_token": "<refresh-token>",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "user_id": 25,
    "email": "student@example.com",
    "full_name": "Example Student",
    "access": ["LMS", "STUDENT"]
  }
}
```

### Start first-time Authenticator setup

```http
POST /auth/authenticator/setup/start
```

Request:

```json
{
  "email": "student@example.com",
  "setup_token": "<64-character-single-use-token>"
}
```

Response:

```json
{
  "issuer": "Inspire College",
  "account_email": "student@example.com",
  "manual_key": "BASE32SECRET",
  "qr_code_data_url": "data:image/png;base64,..."
}
```

The setup token is created by an administrator with:

```http
POST /cms/users/{user_id}/authenticator-setup
```

This endpoint emails the setup link and returns delivery information plus the
single-use setup URL for an administrator fallback:

```json
{
  "email": "student@example.com",
  "setup_token": "<64-character-single-use-token>",
  "expires_at": "2026-08-12T11:00:00Z",
  "setup_url": "http://localhost:5173/?setup=authenticator&email=student%40example.com&token=...",
  "email_sent": true,
  "delivery_message": "Authenticator setup invitation sent by email."
}
```

Invitation delivery uses Mailjet Send API v3.1. Configure these server-only
environment values and restart the API:

```env
MAILJET_API_KEY=<mailjet-api-key>
MAILJET_SECRET_KEY=<mailjet-secret-key>
MAILJET_FROM_EMAIL=lms@your-college-domain.com
MAILJET_FROM_NAME=Inspire College
AUTHENTICATOR_INVITATION_SUBJECT=Set up your Inspire College Authenticator
```

The sender email address or its domain must be validated in Mailjet. Configure
SPF and DKIM for the domain before production use. Mailjet credentials must
never be exposed through a `VITE_` frontend variable.

### Complete first-time setup

```http
POST /auth/authenticator/setup/complete
```

```json
{
  "email": "student@example.com",
  "setup_token": "<64-character-single-use-token>",
  "code": "123456"
}
```

The response contains the normal token response plus `recovery_codes`. Recovery
codes are returned only once and should be stored securely by the user.

### Sign in with a recovery code

```http
POST /auth/authenticator/recovery
```

```json
{
  "email": "student@example.com",
  "recovery_code": "ABCD-EFGH-JKLM"
}
```

Each recovery code can be used once.

### Refresh access token

```http
POST /auth/refresh
```

Request:

```json
{
  "refresh_token": "<refresh-token>"
}
```

The response uses the same token format as Authenticator verification.

### Current user

```http
GET /me
```

Response:

```json
{
  "user_id": 25,
  "email": "student@example.com",
  "full_name": "Example Student",
  "access": ["LMS", "STUDENT"]
}
```

### Logout

```http
POST /auth/logout
```

Request:

```json
{
  "refresh_token": "<refresh-token>"
}
```

Successful response: `204 No Content`.

## CMS-to-LMS single sign-on

### Create LMS SSO ticket

Used by an authenticated CMS user.

```http
POST /auth/sso-ticket
```

Response:

```json
{
  "ticket": "<one-time-64-character-ticket>",
  "expires_in": 60
}
```

The CMS can redirect to:

```text
http://localhost:5174/#sso=<ticket>
```

### Exchange SSO ticket

Used by the LMS frontend.

```http
POST /auth/exchange-sso-ticket
```

Request:

```json
{
  "ticket": "<one-time-64-character-ticket>"
}
```

Response:

```json
{
  "access_token": "<jwt-access-token>",
  "refresh_token": "<refresh-token>",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "user_id": 1,
    "email": "admin@example.com",
    "full_name": "LMS Administrator",
    "access": ["LMS", "ADMIN"]
  }
}
```

The ticket can only be used once and expires after the configured period.

## LMS bootstrap

Returns the current user's LMS role, navigation and enabled features.

```http
GET /lms/bootstrap
```

Access: Any authenticated LMS user.

Example response:

```json
{
  "role": "LECTURER",
  "role_label": "Lecturer",
  "navigation": [
    { "key": "dashboard", "label": "Dashboard", "icon": "home" },
    { "key": "my-courses", "label": "My Courses", "icon": "book" },
    { "key": "my-classes", "label": "My Classes", "icon": "video" }
  ],
  "metrics": [
    {
      "label": "My courses",
      "value": "--",
      "hint": "No courses assigned yet"
    }
  ],
  "enabled_features": ["dashboard", "my-courses", "my-classes"]
}
```

## Programmes

Programmes come from the existing CMS programme catalogue.

### List programmes

```http
GET /lms/programmes
```

Access: `SUPER_ADMIN`, `ADMIN`

Response:

```json
{
  "data": [
    {
      "program_id": 3,
      "code": "BSC-CS",
      "title": "BSc Computer Science",
      "level": "Undergraduate",
      "school": "School of Computing",
      "awarding_body": "Inspire College",
      "duration": "3 Years"
    }
  ]
}
```

The current LMS API does not create programmes. Programme management remains in the CMS.

## Courses

Access: `SUPER_ADMIN`, `ADMIN`

### List courses

```http
GET /lms/courses?page=1&size=20&search=software&program_id=3&status=active
```

| Parameter | Description |
|---|---|
| `page` | Page number, minimum 1 |
| `size` | Results per page, 1–100 |
| `search` | Search course title, code or programme |
| `program_id` | Filter by programme |
| `status` | `draft`, `active` or `archived` |

Response:

```json
{
  "data": [
    {
      "course_id": 12,
      "program_id": 3,
      "program_title": "BSc Computer Science",
      "program_code": "BSC-CS",
      "code": "CS101",
      "title": "Software Development Fundamentals",
      "description": "Introduction to software development.",
      "status": "active",
      "created_at": "2026-08-07T08:30:00Z",
      "updated_at": "2026-08-07T08:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "size": 20,
    "total_items": 1,
    "total_pages": 1
  }
}
```

### Create course

```http
POST /lms/courses
```

Request:

```json
{
  "program_id": 3,
  "code": "CS101",
  "title": "Software Development Fundamentals",
  "description": "Introduction to software development.",
  "status": "active"
}
```

Successful response: `201 Created` with the created course.

### Get course

```http
GET /lms/courses/12
```

### Update course

```http
PUT /lms/courses/12
```

This is a full update, so all fields must be sent:

```json
{
  "program_id": 3,
  "code": "CS101",
  "title": "Software Development Fundamentals",
  "description": "Updated course description.",
  "status": "active"
}
```

### Delete course

```http
DELETE /lms/courses/12
```

Successful response: `204 No Content`.

## Course modules

Access: `SUPER_ADMIN`, `ADMIN`

### List course modules

```http
GET /lms/courses/12/modules
```

Response:

```json
{
  "data": [
    {
      "module_id": 8,
      "course_id": 12,
      "title": "Programming Fundamentals",
      "description": "Variables, conditions and loops.",
      "position": 1,
      "status": "active",
      "created_at": "2026-08-07T09:00:00Z",
      "updated_at": "2026-08-07T09:00:00Z"
    }
  ]
}
```

### Create module

```http
POST /lms/courses/12/modules
```

Request:

```json
{
  "title": "Programming Fundamentals",
  "description": "Variables, conditions and loops.",
  "status": "active"
}
```

The backend automatically assigns the next module position.

### Update module

```http
PUT /lms/modules/8
```

Request:

```json
{
  "title": "Programming Fundamentals",
  "description": "Updated module content.",
  "status": "active"
}
```

### Reorder modules

```http
PUT /lms/courses/12/modules/reorder
```

Request:

```json
{
  "module_ids": [10, 8, 9]
}
```

The request must contain every module in the course exactly once.

### Delete module

```http
DELETE /lms/modules/8
```

Successful response: `204 No Content`. Remaining module positions are reorganized automatically.

## Classes

Access: `SUPER_ADMIN`, `ADMIN`

### List classes

```http
GET /lms/classes?page=1&size=20&search=january&course_id=12&status=active
```

Class statuses:

```text
planned
active
completed
cancelled
```

Delivery modes:

```text
online
hybrid
on_site
```

### Create class

```http
POST /lms/classes
```

Request:

```json
{
  "course_id": 12,
  "code": "CS101-2026-A",
  "name": "January 2026 Intake",
  "description": "Online evening class.",
  "start_date": "2026-08-10",
  "end_date": "2026-12-20",
  "delivery_mode": "online",
  "timezone": "Asia/Colombo",
  "capacity": 50,
  "status": "planned"
}
```

Response:

```json
{
  "class_id": 7,
  "course_id": 12,
  "course_code": "CS101",
  "course_title": "Software Development Fundamentals",
  "program_title": "BSc Computer Science",
  "code": "CS101-2026-A",
  "name": "January 2026 Intake",
  "description": "Online evening class.",
  "start_date": "2026-08-10",
  "end_date": "2026-12-20",
  "delivery_mode": "online",
  "timezone": "Asia/Colombo",
  "capacity": 50,
  "status": "planned",
  "created_at": "2026-08-07T10:00:00Z",
  "updated_at": "2026-08-07T10:00:00Z"
}
```

The end date cannot be before the start date. Capacity must be from 1 to 1,000.

### Update class

```http
PUT /lms/classes/7
```

Send the complete class payload.

### Delete class

```http
DELETE /lms/classes/7
```

Successful response: `204 No Content`.

## Student management

Access: `SUPER_ADMIN`, `ADMIN`

### List students

```http
GET /lms/students?search=example
```

### Create student

```http
POST /lms/students
```

Request:

```json
{
  "full_name": "Example Student",
  "email": "student@example.com",
  "student_number": "STU-2026-001",
  "phone": "+94771234567",
  "notes": "Evening class student"
}
```

Response:

```json
{
  "user_id": 25,
  "full_name": "Example Student",
  "email": "student@example.com",
  "student_number": "STU-2026-001",
  "phone": "+94771234567",
  "notes": "Evening class student",
  "is_active": true,
  "created_at": "2026-08-07T10:15:00Z",
  "authenticator_status": "not_invited",
  "authenticator_invitation_expires_at": null
}
```

Creating a student automatically assigns `LMS` and `STUDENT` access.

### Update student

```http
PUT /lms/students/25
```

Send the complete student payload.

### Activate or deactivate student

```http
PATCH /lms/students/25/active
```

Request:

```json
{
  "is_active": false
}
```

Deactivation revokes the user's refresh tokens.

## Lecturer management

Access: `SUPER_ADMIN`, `ADMIN`

### List lecturers

```http
GET /lms/lecturers?search=smith
```

### Create lecturer

```http
POST /lms/lecturers
```

Request:

```json
{
  "full_name": "Dr Jane Smith",
  "email": "jane.smith@example.com",
  "staff_number": "LEC-2026-001",
  "job_title": "Senior Lecturer",
  "phone": "+94770000000",
  "expertise": "Software engineering and databases"
}
```

Response:

```json
{
  "user_id": 18,
  "full_name": "Dr Jane Smith",
  "email": "jane.smith@example.com",
  "staff_number": "LEC-2026-001",
  "job_title": "Senior Lecturer",
  "phone": "+94770000000",
  "expertise": "Software engineering and databases",
  "is_active": true,
  "created_at": "2026-08-07T10:20:00Z"
}
```

Creating a lecturer automatically assigns `LMS` and `LECTURER` access.

Student and lecturer list responses include `authenticator_status`, with one
of `not_invited`, `invitation_sent`, `invitation_expired`, or `configured`.

### Send or resend an LMS Authenticator invitation

```http
POST /lms/users/25/authenticator-invitation
```

Access: `SUPER_ADMIN`, `ADMIN`. The target must be an active student or
lecturer. This creates a new single-use token, revokes any previous unused
setup token, and sends the secure setup link by email. For a configured user,
this resets their Authenticator and revokes recovery codes and active refresh
sessions. The response uses the delivery format documented under first-time
Authenticator setup, so the admin can copy `setup_url` when `email_sent` is
false.

### Update lecturer

```http
PUT /lms/lecturers/18
```

### Activate or deactivate lecturer

```http
PATCH /lms/lecturers/18/active
```

Request:

```json
{
  "is_active": true
}
```

## Course enrolments and assignments

Access: `SUPER_ADMIN`, `ADMIN`

### Enrol student in course

```http
POST /lms/courses/12/students
```

Request:

```json
{
  "user_id": 25
}
```

Response:

```json
{
  "user_id": 25,
  "full_name": "Example Student",
  "email": "student@example.com",
  "reference_number": "STU-2026-001",
  "secondary_label": "+94771234567",
  "status": "enrolled",
  "assigned_at": "2026-08-07T10:30:00Z"
}
```

### List course students

```http
GET /lms/courses/12/students
```

Response:

```json
{
  "data": [
    {
      "user_id": 25,
      "full_name": "Example Student",
      "email": "student@example.com",
      "reference_number": "STU-2026-001",
      "secondary_label": "+94771234567",
      "status": "enrolled",
      "assigned_at": "2026-08-07T10:30:00Z"
    }
  ],
  "capacity": null,
  "assigned_count": 1
}
```

### Withdraw student from course

```http
DELETE /lms/courses/12/students/25
```

This also removes the student's class assignments belonging to the course.

### Assign lecturer to course

```http
POST /lms/courses/12/lecturers
```

Request:

```json
{
  "user_id": 18
}
```

### List course lecturers

```http
GET /lms/courses/12/lecturers
```

### Remove lecturer from course

```http
DELETE /lms/courses/12/lecturers/18
```

This also removes the lecturer's class assignments belonging to the course.

## Class student and lecturer assignments

Access: `SUPER_ADMIN`, `ADMIN`

A person must first be connected to the course before being assigned to one of its classes.

### Assign student to class

The student must already be enrolled in the class course.

```http
POST /lms/classes/7/students
```

Request:

```json
{
  "user_id": 25
}
```

The backend checks class capacity and prevents duplicate assignments.

### List class students

```http
GET /lms/classes/7/students
```

Response:

```json
{
  "data": [
    {
      "user_id": 25,
      "full_name": "Example Student",
      "email": "student@example.com",
      "reference_number": "STU-2026-001",
      "secondary_label": "+94771234567",
      "status": "assigned",
      "assigned_at": "2026-08-07T10:40:00Z"
    }
  ],
  "capacity": 50,
  "assigned_count": 1
}
```

### Remove student from class

```http
DELETE /lms/classes/7/students/25
```

### Assign lecturer to class

The lecturer must already be assigned to the class course.

```http
POST /lms/classes/7/lecturers
```

Request:

```json
{
  "user_id": 18
}
```

### List class lecturers

```http
GET /lms/classes/7/lecturers
```

### Remove lecturer from class

```http
DELETE /lms/classes/7/lecturers/18
```

## Lecturer and student portal APIs

These endpoints determine the user from the access token. A user ID must not be supplied by the frontend.

### My courses

```http
GET /lms/my/courses
```

Access: `LECTURER`, `STUDENT`

Lecturer response:

```json
{
  "data": [
    {
      "course_id": 12,
      "program_id": 3,
      "program_title": "BSc Computer Science",
      "program_code": "BSC-CS",
      "code": "CS101",
      "title": "Software Development Fundamentals",
      "description": "Introduction to software development.",
      "status": "active",
      "created_at": "2026-08-07T08:30:00Z",
      "updated_at": "2026-08-07T08:30:00Z",
      "module_count": 4,
      "class_count": 2,
      "people_count": 35,
      "people_label": "Students"
    }
  ]
}
```

For students:

- Only actively enrolled, active courses are returned.
- `people_count` represents assigned lecturers.
- `people_label` is `Lecturers`.

### My course details

```http
GET /lms/my/courses/12
```

Response for a student:

```json
{
  "course": {
    "course_id": 12,
    "program_id": 3,
    "program_title": "BSc Computer Science",
    "program_code": "BSC-CS",
    "code": "CS101",
    "title": "Software Development Fundamentals",
    "description": "Introduction to software development.",
    "status": "active",
    "created_at": "2026-08-07T08:30:00Z",
    "updated_at": "2026-08-07T08:30:00Z",
    "module_count": 4,
    "class_count": 1,
    "people_count": 2,
    "people_label": "Lecturers"
  },
  "modules": [
    {
      "module_id": 8,
      "course_id": 12,
      "title": "Programming Fundamentals",
      "description": "Variables, conditions and loops.",
      "position": 1,
      "status": "active",
      "created_at": "2026-08-07T09:00:00Z",
      "updated_at": "2026-08-07T09:00:00Z"
    }
  ],
  "people": [
    {
      "user_id": 18,
      "full_name": "Dr Jane Smith",
      "email": "jane.smith@example.com",
      "reference_number": "LEC-2026-001",
      "secondary_label": "Senior Lecturer",
      "status": "assigned",
      "assigned_at": "2026-08-07T10:35:00Z"
    }
  ],
  "people_label": "Lecturers"
}
```

Students receive only modules with `active` status. Lecturers can see active and draft modules. Accessing an unassigned course returns `404`.

### My classes

```http
GET /lms/my/classes
```

Response:

```json
{
  "data": [
    {
      "class_id": 7,
      "course_id": 12,
      "course_code": "CS101",
      "course_title": "Software Development Fundamentals",
      "program_title": "BSc Computer Science",
      "code": "CS101-2026-A",
      "name": "January 2026 Intake",
      "description": "Online evening class.",
      "start_date": "2026-08-10",
      "end_date": "2026-12-20",
      "delivery_mode": "online",
      "timezone": "Asia/Colombo",
      "capacity": 50,
      "status": "planned",
      "created_at": "2026-08-07T10:00:00Z",
      "updated_at": "2026-08-07T10:00:00Z",
      "people_count": 35,
      "people_label": "Students"
    }
  ]
}
```

For lecturers, `people_count` represents assigned students. For students, it represents assigned lecturers.

### My class details

```http
GET /lms/my/classes/7
```

The current response property name is `class_`:

```json
{
  "class_": {
    "class_id": 7,
    "course_id": 12,
    "course_code": "CS101",
    "course_title": "Software Development Fundamentals",
    "program_title": "BSc Computer Science",
    "code": "CS101-2026-A",
    "name": "January 2026 Intake",
    "description": "Online evening class.",
    "start_date": "2026-08-10",
    "end_date": "2026-12-20",
    "delivery_mode": "online",
    "timezone": "Asia/Colombo",
    "capacity": 50,
    "status": "planned",
    "created_at": "2026-08-07T10:00:00Z",
    "updated_at": "2026-08-07T10:00:00Z",
    "people_count": 1,
    "people_label": "Lecturers"
  },
  "people": [
    {
      "user_id": 18,
      "full_name": "Dr Jane Smith",
      "email": "jane.smith@example.com",
      "reference_number": "LEC-2026-001",
      "secondary_label": "Senior Lecturer",
      "status": "assigned",
      "assigned_at": "2026-08-07T10:45:00Z"
    }
  ],
  "people_label": "Lecturers"
}
```

Accessing an unassigned class returns `404`.

## Google Workspace integration settings

These settings are available only to `SUPER_ADMIN` users. Google OAuth secrets are read from the API environment and are never returned by the API.

### Get Google integration settings

```http
GET /lms/integrations/google
```

Response:

```json
{
  "enabled": false,
  "workspace_domain": "college.edu",
  "embed_enabled": true,
  "calendar_sync_enabled": true,
  "attendance_sync_enabled": true,
  "default_access_type": "restricted",
  "oauth_configured": false,
  "token_encryption_configured": false,
  "oauth_redirect_uri": "http://localhost:8000/api/v1/lms/integrations/google/callback",
  "setup_status": "disabled",
  "updated_at": null
}
```

Possible `setup_status` values:

```text
disabled
credentials_required
security_key_required
ready_for_account_connection
```

### Update Google integration settings

```http
PUT /lms/integrations/google
```

Request:

```json
{
  "enabled": true,
  "workspace_domain": "college.edu",
  "embed_enabled": true,
  "calendar_sync_enabled": true,
  "attendance_sync_enabled": true,
  "default_access_type": "restricted"
}
```

OAuth credentials must be configured in the backend `.env` file:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=<google-client-id>
GOOGLE_OAUTH_CLIENT_SECRET=<google-client-secret>
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/lms/integrations/google/callback
GOOGLE_TOKEN_ENCRYPTION_KEY=<stable-fernet-key>
GOOGLE_OAUTH_STATE_EXPIRE_MINUTES=10
LMS_UI_URL=http://localhost:5174
```

The client secret must not be sent to or stored by the React application.
Generate the encryption key once with:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep this key stable and private. It encrypts lecturer refresh tokens at rest.

### Lecturer Google account status

```http
GET /lms/integrations/google/connection
```

Lecturer response before connection:

```json
{
  "integration_ready": true,
  "connected": false,
  "google_email": null,
  "granted_scopes": [],
  "connected_at": null,
  "message": "Connect your college Google account to schedule online classes."
}
```

### Start lecturer OAuth connection

```http
POST /lms/integrations/google/connect
```

Response:

```json
{
  "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?..."
}
```

Open `authorization_url` in the browser. The API creates a single-use, expiring OAuth state and requests offline access. Google returns to the configured callback:

```http
GET /lms/integrations/google/callback?code=...&state=...
```

The callback verifies the state, exchanges the code, verifies the Google email and Workspace domain, encrypts the refresh token, and redirects to the LMS Online Meetings page. This callback is public because Google calls it, but it accepts only a valid unused state.

### Disconnect lecturer Google account

```http
DELETE /lms/integrations/google/connection
```

The API attempts to revoke the Google refresh token and removes the local connection. Returns `204 No Content`.

## Online meeting scheduling

A lecturer must be assigned to the selected class and must have a connected Google account.

### Schedule Google Meet class

```http
POST /lms/meetings
```

Lecturer request:

```json
{
  "class_id": 1,
  "title": "SE1020 Weekly Lecture",
  "description": "Introduction and module discussion",
  "start_time": "2026-08-15T09:00:00+05:30",
  "end_time": "2026-08-15T11:00:00+05:30"
}
```

The API performs the following actions:

1. Verifies the class is assigned to the lecturer.
2. Refreshes the lecturer's encrypted Google authorization.
3. Creates a Google Meet space using the Super Admin access settings.
4. Enables the native attendance report when configured and supported by the account.
5. Creates a Google Calendar event when calendar sync is enabled.
6. Adds assigned class students as Calendar attendees and sends Google invitations.
7. Stores the meeting link and sync result in `lms_online_meetings`.

Response includes the Meet URL, meeting code, Calendar link, attendee count, notification status, and any non-fatal Calendar sync error.

### List my online meetings

```http
GET /lms/my/meetings
```

Lecturers receive meetings they organized. Students receive meetings for classes assigned to them. Admin and Super Admin users receive the college-wide meeting schedule.

### Edit or reschedule a meeting

```http
PUT /lms/meetings/{meeting_id}
```

Lecturer request:

```json
{
  "title": "SE1020 Rescheduled Lecture",
  "description": "Updated preparation notes",
  "start_time": "2026-08-16T09:00:00+05:30",
  "end_time": "2026-08-16T11:00:00+05:30"
}
```

Only the lecturer who organized a scheduled meeting can edit it. The LMS updates the saved meeting even when Google Calendar synchronization fails, records the failure in `calendar_sync_error`, and sends Calendar updates to all attendees when synchronization succeeds.

### Cancel a meeting

```http
POST /lms/meetings/{meeting_id}/cancel
```

Only the organizing lecturer can cancel a scheduled meeting. The LMS retains the meeting as an audit record with `status: "cancelled"`. When a Calendar event exists, Google deletes it with attendee updates enabled so invited students receive the cancellation.

## Automatic Google Meet attendance

Run `app/modules/lms/sql/attendance.sql` once before using these endpoints. Enable the Google People API in the Google Cloud project so signed-in Google participants can be matched to enrolled LMS email addresses.

The configured default rule is binary:

```text
present = identified enrolled student attended at least the configured percentage
absent  = identified duration is below the threshold, or the student did not join
```

The default threshold is 50%. Super Admin can change `attendance_threshold_percentage` from 1 to 100 in the Google integration settings. Multiple join sessions are added together, but overlapping sessions from multiple devices are merged so time is not double-counted.

Google failures never create new absent results. The attendance session is saved with `sync_status: "failed"`, and the lecturer can retry.

### Synchronize a completed meeting

```http
POST /lms/meetings/{meeting_id}/attendance/sync
```

Role: the lecturer who organized the meeting.

The endpoint is available after the scheduled end time. It:

1. Finds the ended Google conference using the stored `google_space_name`.
2. Retrieves all participants and every join/leave session.
3. Resolves signed-in Google identities to enrolled LMS email addresses.
4. Creates one present or absent record for every active student assigned to the class.
5. Stores anonymous, phone, and unmatched signed-in users separately for review.
6. Preserves lecturer overrides when the meeting is synchronized again.

Example response:

```json
{
  "attendance_session_id": 8,
  "meeting_id": 12,
  "meeting_title": "SE1020 Weekly Lecture",
  "class_id": 3,
  "class_code": "SE1020-A",
  "class_name": "Group A",
  "course_code": "SE1020",
  "course_title": "Software Engineering",
  "actual_start_time": "2026-08-15T09:02:00+05:30",
  "actual_end_time": "2026-08-15T10:58:00+05:30",
  "threshold_percentage": 50,
  "sync_status": "synced",
  "sync_error": null,
  "synced_at": "2026-08-15T11:03:00+05:30",
  "present_count": 24,
  "absent_count": 3,
  "unmatched_participants": [],
  "records": []
}
```

### View meeting attendance

```http
GET /lms/meetings/{meeting_id}/attendance
```

Roles: Super Admin, Admin, or the organizing lecturer. The response includes every student record, duration, percentage, source, and unmatched participants.

### Override a present or absent result

```http
PATCH /lms/attendance/records/{attendance_record_id}
```

Request:

```json
{
  "status": "present",
  "reason": "Student joined using an approved alternate college account"
}
```

Roles: Super Admin, Admin, or the organizing lecturer. The reason is required and the audit fields are retained. A later Google re-sync updates measured duration but does not replace the manual result.

### Student attendance history

```http
GET /lms/my/attendance
```

Role: Student. Returns only the authenticated student's attendance summary and records.

### Attendance reports and CSV export

```http
GET /lms/attendance/report/options
GET /lms/attendance/report
GET /lms/attendance/report/export
```

Roles: Super Admin, Admin, and Lecturer. Super Admin and Admin can report across all synchronized attendance. Lecturers are always restricted to meetings they organized, even when query parameters contain another lecturer ID.

Supported report filters:

| Query field | Meaning |
|---|---|
| `program_id` | CMS programme linked to the LMS course |
| `course_id` | LMS course |
| `class_id` | LMS class |
| `student_user_id` | Enrolled student |
| `lecturer_user_id` | Organizing lecturer; Admin and Super Admin only in the UI |
| `date_from`, `date_to` | Inclusive meeting date range in `YYYY-MM-DD` format |
| `status` | `present` or `absent` |
| `search` | Student, email, number, class, course, or meeting text |
| `page`, `size` | JSON report pagination; size is limited to 200 |

The JSON report contains present and absent totals, present rate, average joined percentage, unique student count, meeting count, and separate record fields.

The export endpoint applies the same filters and returns `text/csv`. It has 30 separate columns covering result, duration, meeting, programme, course, class, lecturer, student, and synchronization fields. Spreadsheet formula prefixes in user-entered text are escaped before export.

## Endpoint summary

### General LMS

| Method | Endpoint | Roles |
|---|---|---|
| GET | `/lms/bootstrap` | Any LMS user |
| GET | `/lms/programmes` | Admin, Super Admin |
| GET, PUT | `/lms/integrations/google` | Super Admin |
| GET, DELETE | `/lms/integrations/google/connection` | Lecturer |
| POST | `/lms/integrations/google/connect` | Lecturer |
| GET | `/lms/integrations/google/callback` | Public, state protected |
| POST | `/lms/meetings` | Lecturer |
| PUT | `/lms/meetings/{meeting_id}` | Organizing lecturer |
| POST | `/lms/meetings/{meeting_id}/cancel` | Organizing lecturer |
| GET | `/lms/my/meetings` | Super Admin, Admin, Lecturer, Student |
| POST | `/lms/meetings/{meeting_id}/attendance/sync` | Organizing lecturer |
| GET | `/lms/meetings/{meeting_id}/attendance` | Super Admin, Admin, organizing lecturer |
| PATCH | `/lms/attendance/records/{attendance_record_id}` | Super Admin, Admin, organizing lecturer |
| GET | `/lms/my/attendance` | Student |
| GET | `/lms/attendance/report/options` | Super Admin, Admin, Lecturer |
| GET | `/lms/attendance/report` | Super Admin, Admin, Lecturer |
| GET | `/lms/attendance/report/export` | Super Admin, Admin, Lecturer |

### Courses and modules

| Method | Endpoint | Roles |
|---|---|---|
| GET, POST | `/lms/courses` | Admin, Super Admin |
| GET, PUT, DELETE | `/lms/courses/{course_id}` | Admin, Super Admin |
| GET, POST | `/lms/courses/{course_id}/modules` | Admin, Super Admin |
| PUT, DELETE | `/lms/modules/{module_id}` | Admin, Super Admin |
| PUT | `/lms/courses/{course_id}/modules/reorder` | Admin, Super Admin |

### Classes and people

| Method | Endpoint | Roles |
|---|---|---|
| GET, POST | `/lms/classes` | Admin, Super Admin |
| PUT, DELETE | `/lms/classes/{class_id}` | Admin, Super Admin |
| GET, POST | `/lms/students` | Admin, Super Admin |
| PUT | `/lms/students/{user_id}` | Admin, Super Admin |
| PATCH | `/lms/students/{user_id}/active` | Admin, Super Admin |
| GET, POST | `/lms/lecturers` | Admin, Super Admin |
| PUT | `/lms/lecturers/{user_id}` | Admin, Super Admin |
| PATCH | `/lms/lecturers/{user_id}/active` | Admin, Super Admin |

### Assignments

| Method | Endpoint | Purpose |
|---|---|---|
| GET, POST | `/lms/courses/{course_id}/students` | List or enrol students |
| DELETE | `/lms/courses/{course_id}/students/{user_id}` | Withdraw student |
| GET, POST | `/lms/courses/{course_id}/lecturers` | List or assign lecturers |
| DELETE | `/lms/courses/{course_id}/lecturers/{user_id}` | Remove lecturer |
| GET, POST | `/lms/classes/{class_id}/students` | List or assign students |
| DELETE | `/lms/classes/{class_id}/students/{user_id}` | Remove student |
| GET, POST | `/lms/classes/{class_id}/lecturers` | List or assign lecturers |
| DELETE | `/lms/classes/{class_id}/lecturers/{user_id}` | Remove lecturer |

### Lecturer and student portal

| Method | Endpoint | Roles |
|---|---|---|
| GET | `/lms/my/courses` | Lecturer, Student |
| GET | `/lms/my/courses/{course_id}` | Lecturer, Student |
| GET | `/lms/my/classes` | Lecturer, Student |
| GET | `/lms/my/classes/{class_id}` | Lecturer, Student |

## Common error responses

### Validation error — 400

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Student must be enrolled in the class course first"
  }
}
```

Field validation example:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {
        "field": "capacity",
        "issue": "Input should be greater than or equal to 1"
      }
    ]
  }
}
```

### Authentication error — 401

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Authentication is required"
  }
}
```

### Permission error — 403

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Your LMS role cannot perform this action"
  }
}
```

### Not found — 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Course 12 not found"
  }
}
```

### Conflict — 409

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "Student is already enrolled in this course"
  }
}
```

## Recommended setup flow

1. Authenticate as an Admin or Super Admin.
2. Load an existing CMS programme.
3. Create a course.
4. Add course modules.
5. Create a class.
6. Create student and lecturer profiles.
7. Enrol the student in the course.
8. Assign the lecturer to the course.
9. Assign the student to the class.
10. Assign the lecturer to the class.
11. Sign in as the student or lecturer.
12. Load `/lms/my/courses` and `/lms/my/classes`.

The required assignment sequence is:

```http
POST /lms/courses/12/students
POST /lms/courses/12/lecturers
POST /lms/classes/7/students
POST /lms/classes/7/lecturers
```

Course assignment must happen before class assignment.

## Student learning progress

Apply `app/modules/lms/sql/learning_progress.sql` before using these endpoints.

### Record student learning progress

```http
POST /api/v1/lms/my/learning-items/45/progress
Authorization: Bearer <student-token>
Content-Type: application/json

{
  "position_seconds": 126,
  "duration_seconds": 1200,
  "watched_seconds_delta": 10,
  "event": "heartbeat"
}
```

The server bounds each watch-time increment against elapsed server time. Video
completion is calculated at 95 percent watched; a client cannot submit its own
completion percentage.

### View the signed-in student's course progress

```http
GET /api/v1/lms/my/courses/12/progress
Authorization: Bearer <student-token>
```

### Lecturer: view one enrolled student's course progress

```http
GET /api/v1/lms/studio/courses/12/students/84/progress
Authorization: Bearer <lecturer-token>
```

Only a lecturer assigned to the course can access this report. The response
contains course and section percentages plus item-by-item completion records.
