# CRM API for external systems

This is a server-to-server API for reading and managing leads in the Inspire CRM. It uses one non-expiring token instead of a staff login token. Keep it on the receiving system's backend; never put it in browser JavaScript, a mobile app, or a public repository. The token permits creating, editing, and permanently deleting leads, so restrict access to it.

## Hosted URL and availability

Production base URL: `https://api.inspirecollege.lk/api/v1/integrations/crm`

The hosted `/health` endpoint returned `200 {"status":"ok"}` on 12 October 2026. The integration endpoints become available when this backend change is deployed and `CRM_INTEGRATION_API_TOKEN` is configured on the hosted API. No database migration is needed.

## One-time token setup

Generate a random token once:

```bash
python -c "import secrets; print('iccrm_' + secrets.token_urlsafe(32))"
```

Set the same value as `CRM_INTEGRATION_API_TOKEN` in the hosted API's secret environment and as `INSPIRE_CRM_API_KEY` in the receiving backend's secret environment. Do not commit the token to Git. The API rejects requests while its token is unset or shorter than 32 characters.

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

## Generated reference and limitations

After deployment, interactive API docs are at `https://api.inspirecollege.lk/docs` and the OpenAPI JSON is at `https://api.inspirecollege.lk/openapi.json`. Search for the `crm-integrations` tag. The integration key works on the five lead routes in this guide. This API is intended for a backend client over HTTPS. Its key is not a user identity and does not restrict results to one counsellor.
