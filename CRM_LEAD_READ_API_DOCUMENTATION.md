# CRM lead retrieval API: complete reference

The backend reads leads from its own `crm_leads` table. This document covers every read endpoint needed to list leads, find filter choices, and retrieve one lead with its activity history. These endpoints are implemented in `app/modules/crm/router.py` and `app/modules/crm/service.py`; the CMS client calls them through `crmApi.listLeads()` and `crmApi.getLead()`.

## Base URL and authentication

Local base URL: `http://localhost:8000/api/v1/cms` (replace the host and port with your deployed API URL). The same CRM router is also mounted at `/api/v1/crm`.

Send a CMS access token with every request:

```http
Authorization: Bearer <access_token>
```

The token must contain `CRM`, `COUNSELLOR`, `ADMIN`, or `SUPER_ADMIN` access. Obtain the access token by signing in through the existing CMS flow. Counsellors see only their assigned leads. The API ignores a counsellor's requested `counsellor_id` and `unassigned` filters and enforces their own user ID. An individual lead assigned to someone else returns `403` for a counsellor. Keep the token on your backend or in the existing authenticated CMS client; do not expose a service credential in a public website.

All endpoints below use `GET`, require no request body, and return JSON on success. A successful call returns `200 OK`.

## List leads

`GET /crm/leads`

Returns a page of lead summaries and an unpaginated `total`. The response has no outer `data` envelope beyond the `data` array shown below.

| Query parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `page` | integer, minimum 1 | `1` | Page number |
| `size` | integer, 1 to 200 | `50` | Results per page |
| `search` | string, max 100 characters | none | Case-insensitive substring search across name, email, phone, city, programme, course, awarding body, and counsellor name |
| `stage` | string | none | Exact pipeline stage; values are listed below |
| `source` | string | none | Exact lead source |
| `priority` | `high`, `medium`, `low` | none | Priority; other values have no filtering effect |
| `counsellor_id` | integer | none | Assigned counsellor; overridden for counsellor-only users |
| `unassigned` | boolean | `false` | Only unassigned leads; overridden for counsellor-only users |
| `include_archived` | boolean | `false` | Include archived leads |
| `programme` | string | none | Exact match against interested programme or course |
| `awarding_body` | string | none | Exact awarding body |
| `date_from`, `date_to` | `YYYY-MM-DD` | none | Inclusive created-date range |
| `sort` | `newest`, `oldest`, `updated`, `priority` | `newest` | Result order |
| `city`, `country`, `campaign`, `intake` | string | none | Case-insensitive exact match |
| `modified_from`, `modified_to` | `YYYY-MM-DD` | none | Inclusive updated-date range |
| `contacted_from`, `contacted_to` | `YYYY-MM-DD` | none | Inclusive last-contacted-date range |
| `followup_from`, `followup_to` | `YYYY-MM-DD` | none | Inclusive follow-up-date range |
| `days_since_contact` | integer, minimum 0 | none | Leads never contacted or last contacted before the current UTC time minus this many days |
| `followup_state` | `missing`, `overdue`, `due_today` | none | Missing date, date before current UTC time, or date within the current UTC day; other values have no filtering effect |

Filters can be combined. A page beyond the available results returns `{"data":[],"total":<matching_count>}`. `total` counts all matching leads before pagination. `include_archived=false` excludes archived leads. The created-date filter handles imported CRM records and newer API-created records according to the backend's stored timestamp conventions; date-time fields in the response can lack a time-zone offset.

Known stage values: `new_lead`, `uncontactable`, `contacted`, `future_prospect`, `not_interested`, `lost_to_competitor`, `cant_afford`, `enrolled`.

```bash
curl -G 'http://localhost:8000/api/v1/cms/crm/leads' \
  -H 'Authorization: Bearer <access_token>' \
  --data-urlencode 'page=1' \
  --data-urlencode 'size=20' \
  --data-urlencode 'stage=new_lead'
```

Example response (other summary fields may also be present):

```json
{
  "data": [
    {
      "lead_id": 123,
      "external_record_id": null,
      "full_name": "Example Student",
      "email": "student@example.com",
      "phone": "0771234567",
      "city": "Colombo",
      "country": "Sri Lanka",
      "interested_programme": "Business",
      "interested_course": "Business Management",
      "awarding_body": null,
      "source": "website_admission",
      "stage": "new_lead",
      "campaign": null,
      "academic_course_id": null,
      "last_contacted_at": null,
      "last_activity_at": null,
      "followup_count": 0,
      "priority": "medium",
      "amount": null,
      "counsellor_name": null,
      "assigned_counsellor_id": null,
      "assigned_at": null,
      "followup_date": null,
      "followup_type": null,
      "created_at": "2026-10-12T09:30:00",
      "updated_at": "2026-10-12T09:30:00"
    }
  ],
  "total": 1
}
```

The example above contains all 26 fields in each summary. Optional fields are serialized as `null` when absent.

## Available filter choices

`GET /crm/leads/filters`

Returns the distinct values available to populate filter controls. It has no query parameters. Counsellor-only users receive values from their own non-archived leads; other permitted users receive values from all non-archived leads.

```json
{
  "awarding_bodies": ["Example Awarding Body"],
  "programmes": ["Business Management"],
  "sources": ["website_admission"],
  "cities": ["Colombo"],
  "countries": ["Sri Lanka"],
  "campaigns": ["October Intake"],
  "intakes": ["October 2026"]
}
```

Each property is an array of strings; an empty array means there are no values. The API caps `programmes`, `cities`, `countries`, `campaigns`, and `intakes` at 200 values each.

## Get full lead details

`GET /crm/leads/{lead_id}`

`lead_id` is the numeric ID returned by the list endpoint. This request returns the lead object directly, including its activity timeline in `activities` (newest first). It also includes contact information, course interest, assignment, follow-up, status reasons, fees and payment fields, notes, and timestamps. Nullable fields appear as `null` when empty.

```bash
curl 'http://localhost:8000/api/v1/cms/crm/leads/123' \
  -H 'Authorization: Bearer <access_token>'
```

Selected fields from an example response:

```json
{
  "lead_id": 123,
  "full_name": "Example Student",
  "email": "student@example.com",
  "phone": "0771234567",
  "source": "website_admission",
  "stage": "new_lead",
  "priority": "medium",
  "interested_programme": "Business",
  "interested_course": "Business Management",
  "assigned_counsellor_id": 17,
  "counsellor_name": "Example Counsellor",
  "followup_date": null,
  "notes": null,
  "is_archived": false,
  "created_at": "2026-10-12T09:30:00",
  "updated_at": "2026-10-12T09:30:00",
  "activities": [
    {
      "activity_id": 456,
      "lead_id": 123,
      "activity_type": "note",
      "content": "Lead created",
      "counsellor_name": "Example Counsellor",
      "counsellor_id": 17,
      "created_at": "2026-10-12T09:30:00"
    }
  ]
}
```

The example is abbreviated. The full 64-field response contract follows; fields are returned even when their value is `null`. `string?`, `number?`, and `integer?` mean nullable. Date-times are strings in ISO 8601 form; do not infer a time zone when a timestamp has no offset.

| Field | Type | Meaning |
| --- | --- | --- |
| `lead_id` | integer | Internal CRM lead ID |
| `external_record_id` | string? | Imported or external CRM record ID |
| `social_lead_id` | string? | Source platform lead ID |
| `full_name` | string | Lead name |
| `email`, `phone`, `whatsapp` | string? | Contact details |
| `address_line1`, `address_line2` | string? | Street address |
| `city`, `district`, `country` | string? | Location |
| `location_lat`, `location_lng` | number? | Location coordinates |
| `nationality` | string? | Nationality |
| `highest_qualification` | string? | Prior qualification |
| `school`, `faculty` | string? | Academic context |
| `student_status`, `student_id` | string? | Student status and number |
| `student_user_id` | integer? | Linked student user ID |
| `parents_occupation`, `parents_email` | string? | Parent details |
| `interested_programme`, `interested_course`, `awarding_body` | string? | Programme interest |
| `academic_course_id` | integer? | Linked course ID |
| `programme_duration`, `intake`, `expected_intake`, `expected_month` | string? | Study duration and intake information |
| `source`, `stage`, `priority` | string | Origin, pipeline stage, and priority |
| `campaign` | string? | Campaign label |
| `message`, `notes` | string? | Enquiry message and internal notes |
| `status_reason`, `status_remarks`, `affordability_reason`, `delay_reason` | string? | Reasons and remarks for pipeline status |
| `programme_fee`, `registration_fee`, `amount`, `amount_paid` | number? | Fee and payment amounts |
| `payment_plan`, `payment_status` | string? | Payment arrangement and state |
| `payment_date` | date-time? | Payment time |
| `enrollment_email_status`, `enrollment_email_error` | string? | Enrollment email delivery state and error |
| `enrollment_email_sent_at`, `enrolled_at` | date-time? | Enrollment mail and enrollment times |
| `assigned_counsellor_id` | integer? | Assigned user ID |
| `counsellor_name` | string? | Assigned counsellor name |
| `assigned_at` | date-time? | Assignment time |
| `last_contacted_at`, `last_activity_at` | date-time? | Recent contact and activity times |
| `followup_count` | integer | Number of follow-ups |
| `followup_date` | date-time? | Next follow-up time |
| `followup_type` | string? | Next follow-up channel or type |
| `is_archived` | boolean | Archive flag |
| `created_at`, `updated_at` | date-time | Creation and last update times |
| `activities` | array of objects | Activity timeline, newest first |

Each `activities` item has `activity_id` (integer), `lead_id` (integer), `activity_type` (string), `content` (string), `counsellor_name` (string or null), `counsellor_id` (integer or null), and `created_at` (date-time). An empty timeline is `[]`.

## Errors

| HTTP status | Meaning |
| --- | --- |
| `400` | Invalid path or query value, with `VALIDATION_ERROR` |
| `401` | Missing, invalid, or expired bearer token |
| `403` | Token lacks CRM access or a counsellor requests another person's lead |
| `404` | Lead ID does not exist (for a user permitted to query it) |

Errors use this shape:

```json
{"error":{"code":"NOT_FOUND","message":"Lead 123 not found"}}
```

Validation errors can also include an `error.details` array of objects with `field` and `issue`. The backend's generated OpenAPI reference is available at `/docs` and `/openapi.json` on the same host.

## Existing CMS connection

The CMS reads the API base URL from `VITE_API_BASE_URL` (default `http://localhost:8000/api/v1/cms`). Its `crmApi.listLeads()` and `crmApi.getLead(id)` methods use the same authenticated API client as the rest of the CMS. Set `VITE_API_BASE_URL` to your deployed backend URL and sign in with a CRM-authorized account. No additional endpoint or database migration is required for lead reads.
