# Hosted CRM integration API — complete reference

This is a server-to-server API for reading and managing leads in the Inspire CRM. It uses one non-expiring token instead of a staff login token. Keep it on the receiving system's backend; never put it in browser JavaScript, a mobile app, or a public repository. The token permits creating, editing, and permanently deleting leads, so restrict access to it.

## Hosted URL and availability

Production base URL: `https://api.inspirecollege.lk/api/v1/integrations/crm`

The hosted API was verified on 12 October 2026: health returned `200`, lead list `200`, create `201`, edit `200`, and delete `204`. The temporary test lead was deleted and then returned `404`.

| Operation | Method | Full hosted URL | Success |
| --- | --- | --- | --- |
| List leads | `GET` | `https://api.inspirecollege.lk/api/v1/integrations/crm/leads` | `200` |
| Create lead | `POST` | `https://api.inspirecollege.lk/api/v1/integrations/crm/leads` | `201` |
| Get lead | `GET` | `https://api.inspirecollege.lk/api/v1/integrations/crm/leads/{lead_id}` | `200` |
| Edit lead | `PUT` | `https://api.inspirecollege.lk/api/v1/integrations/crm/leads/{lead_id}` | `200` |
| Delete lead | `DELETE` | `https://api.inspirecollege.lk/api/v1/integrations/crm/leads/{lead_id}` | `204` |

`lead_id` is the numeric ID supplied by the API. All five routes use HTTPS and the token header below. `POST` and `PUT` take JSON request bodies. Successful `GET`, `POST`, and `PUT` responses are JSON; `DELETE` has no response body.

## Token setup for a connecting backend

The hosted API already has a working token. Copy the current `CRM_INTEGRATION_API_TOKEN` value from the API operator's secret store or the local, Git-ignored `.env` file into the connecting backend's secret environment as `INSPIRE_CRM_API_KEY`. Use that value in the `X-CRM-API-Key` header. Do not put it in browser code, mobile apps, logs, or source control.

Only when rotating the token, generate a new random value:

```bash
python -c "import secrets; print('iccrm_' + secrets.token_urlsafe(32))"
```

Set the new value as `CRM_INTEGRATION_API_TOKEN` on the hosted API and as `INSPIRE_CRM_API_KEY` in the receiving backend. The API rejects requests while its token is unset or shorter than 32 characters.

The token does not expire automatically. To replace it, set a new value on the hosted API and receiving backend. Changing the hosted value immediately makes the old token invalid.

## Authentication

Send the key as an HTTPS request header on every call:

```http
X-CRM-API-Key: <configured token>
```

Staff `Authorization: Bearer` tokens are not accepted on these integration routes. A missing or incorrect token returns `401`:

```json
{"error":{"code":"UNAUTHORIZED","message":"Invalid CRM API key"}}
```

### Postman setup

Create a Postman environment with these variables:

| Variable | Value |
| --- | --- |
| `baseUrl` | `https://api.inspirecollege.lk/api/v1/integrations/crm` |
| `crmToken` | The existing `CRM_INTEGRATION_API_TOKEN` value from the API host or local `.env` |
| `leadId` | An ID from the list or create response |

For each request, add `X-CRM-API-Key: {{crmToken}}` under **Headers**. For `POST` and `PUT`, select **Body → raw → JSON**; Postman will set `Content-Type: application/json`. A ready-to-import collection is available in [CRM_INTEGRATION_POSTMAN_COLLECTION.json](CRM_INTEGRATION_POSTMAN_COLLECTION.json). It contains placeholders and no secret token.

## List leads

`GET /leads`

Returns lead summaries in `data` and the total matching count in `total`. Default order is most recently updated first, then highest lead ID. All query filters can be combined.

| Query parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `page` | integer, minimum 1 | `1` | Page number |
| `size` | integer, 1 to 200 | `50` | Leads per page |
| `stage` | string | none | Exact CRM stage |
| `source` | string | none | Exact source |
| `search` | string, maximum 100 characters | none | Search name, contact, location, programme, course, awarding body, and counsellor |
| `modified_from` | `YYYY-MM-DD` | none | Include leads updated on or after this date |
| `modified_to` | `YYYY-MM-DD` | none | Include leads updated through this date |
| `include_archived` | boolean | `false` | Include archived leads |

Use both `modified_from` and pagination to sync changes. Dates are inclusive. If a lead changes while pages are being read, offset pagination can shift, so deduplicate by `lead_id` and perform a short overlapping rescan. The API does not currently provide a cursor or deletion feed.

```bash
curl -i -G 'https://api.inspirecollege.lk/api/v1/integrations/crm/leads' \
  -H "X-CRM-API-Key: $INSPIRE_CRM_API_KEY" \
  --data-urlencode 'page=1' \
  --data-urlencode 'size=50' \
  --data-urlencode 'modified_from=2026-10-01'
```

Response shape:

```json
{
  "data": [
    {
      "lead_id": 123,
      "external_record_id": null,
      "full_name": "Example Student",
      "email": "student@example.com",
      "phone": "0771234567",
      "source": "website_admission",
      "stage": "new_lead",
      "priority": "medium",
      "created_at": "2026-10-12T09:30:00",
      "updated_at": "2026-10-12T09:30:00"
    }
  ],
  "total": 1
}
```

The example lead is shortened. The full summary schema is `CrmLeadSummary` in the generated OpenAPI document.

## Get one lead

`GET /leads/{lead_id}`

Returns the full `CrmLeadOut` object directly, including the `activities` array, internal notes, contact and parent details, assignment, fees, and payment fields. Treat the response as sensitive personal data and grant the key only to systems authorized to receive all these fields. `lead_id` comes from the list endpoint.

```bash
curl -i 'https://api.inspirecollege.lk/api/v1/integrations/crm/leads/123' \
  -H "X-CRM-API-Key: $INSPIRE_CRM_API_KEY"
```

An unknown lead returns `404` with `NOT_FOUND`. Malformed IDs and invalid query values return `400` with `VALIDATION_ERROR`. The response fields and activity item fields are described in [CRM_LEAD_READ_API_DOCUMENTATION.md](CRM_LEAD_READ_API_DOCUMENTATION.md).

## Create a lead

`POST /leads` returns `201 Created` and the full lead object. Only `full_name` is required. Other accepted fields are defined by `CrmLeadCreate` in the generated OpenAPI reference. If `source` is omitted, it defaults to `api_integration`. The CRM may automatically assign the lead to a counsellor according to its assignment settings.

In Postman, choose **Body → raw → JSON**, and send `Content-Type: application/json` with the token header:

```json
{
  "full_name": "Example Student",
  "email": "student@example.com",
  "phone": "0771234567",
  "interested_course": "Business Management",
  "source": "partner_system"
}
```

```bash
curl -i -X POST 'https://api.inspirecollege.lk/api/v1/integrations/crm/leads' \
  -H "X-CRM-API-Key: $INSPIRE_CRM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"full_name":"Example Student","email":"student@example.com","source":"partner_system"}'
```

Save the returned `lead_id` for later requests. Creating an `enrolled` lead must meet the CRM's enrollment requirements.

## Edit a lead

`PUT /leads/{lead_id}` returns `200 OK` and the full updated lead. Send only fields you want to change; omitted fields stay as they are. The body uses `CrmLeadUpdate`. The CRM records tracked changes in the lead's activity history. Setting `stage` to `enrolled` may send an enrollment email.

```json
{"city":"Kandy","priority":"high","notes":"Updated by partner system"}
```

```bash
curl -i -X PUT 'https://api.inspirecollege.lk/api/v1/integrations/crm/leads/123' \
  -H "X-CRM-API-Key: $INSPIRE_CRM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"city":"Kandy","priority":"high"}'
```

## Delete a lead

`DELETE /leads/{lead_id}` returns `204 No Content`. This permanently deletes the lead and its activity history. It is not an archive action. An unknown ID returns `404`.

```bash
curl -i -X DELETE 'https://api.inspirecollege.lk/api/v1/integrations/crm/leads/123' \
  -H "X-CRM-API-Key: $INSPIRE_CRM_API_KEY"
```

For a reversible removal from normal list views, use `PUT /leads/{lead_id}` with `{"is_archived":true}` instead.

## Complete request field reference

`POST` accepts the **Create** fields and `PUT` accepts the **Edit** fields. All fields except `full_name` on create are optional. A field omitted from `PUT` remains unchanged. Use JSON `null` only to clear a nullable field; do not send `null` for `full_name`, `source`, `stage`, or `priority`. Unrecognized fields are ignored by the current schema, so check spelling against this table.

| Fields | JSON type | Create | Edit | Notes |
| --- | --- | --- | --- | --- |
| `full_name` | string | required | yes | Lead name |
| `created_at` | date-time string | yes | yes | Cannot be in the future; omit for current time |
| `email`, `phone`, `whatsapp` | string or null | yes | yes | Contact details |
| `city`, `district`, `country` | string or null | yes | yes | Location |
| `location_lat`, `location_lng` | number or null | yes | no | Coordinates can be supplied only on create |
| `highest_qualification`, `school`, `nationality`, `faculty`, `student_status` | string or null | yes | yes | Background details |
| `parents_occupation`, `parents_email`, `address_line1`, `address_line2` | string or null | yes | yes | Parent and address details |
| `interested_programme`, `interested_course`, `awarding_body` | string or null | yes | yes | Course interest |
| `academic_course_id` | integer or null | yes | yes | Must be a valid active course; selecting it can fill course details |
| `programme_fee`, `registration_fee`, `amount` | number or null | yes | yes | Fee and amount fields |
| `programme_duration`, `intake`, `expected_intake`, `expected_month`, `payment_plan` | string or null | yes | yes | Study and payment plan details |
| `message`, `campaign`, `notes` | string or null | yes | yes | Enquiry and internal notes |
| `source` | string | yes | yes | Defaults to `api_integration` if omitted on create |
| `stage` | string | yes | yes | Defaults to `new_lead`; values below |
| `status_reason`, `status_remarks`, `affordability_reason`, `delay_reason` | string or null | yes | yes | Required combinations depend on stage |
| `followup_date` | date-time string or null | yes | yes | Next follow-up time |
| `priority` | string | yes | yes | Defaults to `medium`; use `low`, `medium`, or `high` |
| `social_lead_id`, `external_record_id` | string or null | yes | yes | Source IDs; `external_record_id` is unique when present |
| `assigned_counsellor_id` | integer or null | yes | yes | Must identify a registered counsellor; omission on create allows auto-assignment |
| `counsellor_name`, `followup_type` | string or null | no | yes | `counsellor_name` is managed automatically when assignment ID changes |
| `is_archived` | boolean | no | yes | `true` hides the lead from the default list; can be reversed |

**Stage values:** `new_lead`, `uncontactable`, `contactable`, `future_prospect`, `not_interested`, `lost_to_competitor`, `cant_afford`, `enrolled`. For `not_interested` or `lost_to_competitor`, supply a valid `status_reason`; for `cant_afford`, supply an `affordability_reason`; for `future_prospect`, supply a `delay_reason`. If a reason is `Other`, supply `status_remarks`. `enrolled` requires a valid email and may trigger an enrollment email.

| Field | Valid values when required |
| --- | --- |
| `status_reason` for `not_interested` or `lost_to_competitor` | `Fees Beyond Expectations`; `Programme Not Available`; `Joined Another Institute`; `Entry Requirements Issue`; `Prefers Physical Classes`; `Prefers Another Study Mode`; `Programme Duration Not Suitable`; `Schedule Not Suitable`; `Qualification / Recognition Concern`; `Parent or Guardian Not Interested`; `Decided to Postpone Studies`; `No Longer Interested in Higher Education`; `Other` |
| `affordability_reason` for `cant_afford` | `Programme Fee Too High`; `Initial Payment Too High`; `Monthly Installment Too High`; `Registration Fee Issue`; `Currently No Financial Capacity`; `Waiting for Salary / Income`; `Waiting for Parent / Sponsor Support`; `Looking for Scholarship / Discount`; `Competitor Offering Lower Price`; `Other` |
| `delay_reason` for `future_prospect` | `Waiting for Exam Results`; `Financial Planning`; `Currently Studying Another Programme`; `Planning for a Future Intake`; `Waiting for Employment`; `Need Parent / Sponsor Approval`; `Currently Overseas`; `Other` |

`created_at` accepts an ISO 8601 date-time. A value without an offset is interpreted as Sri Lanka local time; a value with an offset is converted to UTC for a newly created lead. Stored response timestamps can be offset-free, and historical imported leads may follow older timestamp conventions. Do not infer a timezone from an offset-free response.

`POST` is not idempotent: repeating it can create another lead. Save the returned `lead_id` in the receiving system. A duplicate `external_record_id` violates a database uniqueness constraint; there is no upsert endpoint or idempotency-key header.

## Complete response field reference

The list response is `{"data":[<lead summaries>],"total":<integer>}`. `total` is the number of all matching leads before pagination. Every summary in `data` includes these 26 fields:

| Fields | Type | Meaning |
| --- | --- | --- |
| `lead_id` | integer | CRM ID; use for detail, edit, and delete |
| `external_record_id` | string or null | External record ID |
| `full_name` | string | Lead name |
| `email`, `phone`, `city`, `country` | string or null | Contact and location |
| `interested_programme`, `interested_course`, `awarding_body` | string or null | Study interest |
| `source`, `stage`, `priority` | string | Origin, status, priority |
| `campaign` | string or null | Campaign label |
| `academic_course_id` | integer or null | Linked course ID |
| `last_contacted_at`, `last_activity_at` | date-time string or null | Recent contact and activity |
| `followup_count` | integer | Number of follow-ups |
| `amount` | number or null | Lead amount |
| `counsellor_name` | string or null | Assigned counsellor |
| `assigned_counsellor_id` | integer or null | Assigned counsellor ID |
| `assigned_at`, `followup_date` | date-time string or null | Assignment and follow-up time |
| `followup_type` | string or null | Follow-up type |
| `created_at`, `updated_at` | date-time string | Creation and last update |

`GET` detail, `POST`, and `PUT` return the full lead object directly. It includes all 26 summary fields above plus these 38 fields:

| Fields | Type | Meaning |
| --- | --- | --- |
| `whatsapp`, `district`, `highest_qualification`, `school`, `nationality`, `faculty`, `student_status` | string or null | Additional contact and background |
| `location_lat`, `location_lng` | number or null | Coordinates |
| `parents_occupation`, `parents_email`, `address_line1`, `address_line2` | string or null | Parent and address details |
| `message`, `notes` | string or null | Enquiry and internal notes |
| `status_reason`, `status_remarks`, `affordability_reason`, `delay_reason` | string or null | Status reasons |
| `expected_intake`, `expected_month`, `intake`, `programme_duration`, `payment_plan` | string or null | Study and payment plan information |
| `programme_fee`, `registration_fee`, `amount_paid` | number or null | Fee and payment amounts |
| `payment_status`, `enrollment_email_status`, `enrollment_email_error`, `student_id` | string or null | Payment, mail, and student state |
| `student_user_id` | integer or null | Linked student user |
| `payment_date`, `enrollment_email_sent_at`, `enrolled_at` | date-time string or null | Payment and enrollment times |
| `is_archived` | boolean | Archive flag |
| `social_lead_id` | string or null | Source platform ID |
| `activities` | array | Lead activity history, newest first |

Each `activities` entry contains `activity_id` (integer), `lead_id` (integer), `activity_type` (string), `content` (string), `counsellor_name` (string or null), `counsellor_id` (integer or null), and `created_at` (date-time string). The array is empty when there is no history. Optional fields in responses are present as `null` when empty.

## Errors and integration behavior

| Status | When it occurs |
| --- | --- |
| `400` | Invalid path/query/body value or CRM validation failure; code `VALIDATION_ERROR` |
| `401` | Missing or wrong `X-CRM-API-Key`; code `UNAUTHORIZED` |
| `404` | Lead ID does not exist; code `NOT_FOUND` |
| `204` | Successful delete; no JSON body |

Error JSON uses `{"error":{"code":"...","message":"..."}}`; request validation errors can also include `error.details` with `field` and `issue`. A server failure may return `500`.

The list uses page-number pagination (`page`, `size`). Sorting is by `updated_at` descending, then `lead_id` descending. For incremental syncing, use `modified_from` with an overlap, page through results, and deduplicate by `lead_id`. Offset pages can shift while records change. Deleted leads do not appear in a deletion feed. Use `is_archived=true` when you need a reversible removal.

## Generated reference

Interactive API docs are at `https://api.inspirecollege.lk/docs` and the OpenAPI JSON is at `https://api.inspirecollege.lk/openapi.json`. Search for the `crm-integrations` tag. The token applies to the five lead routes in this guide, grants access across all counsellors, and should be used only by a trusted backend.
