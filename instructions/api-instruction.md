# CMS API Specification — Course Management

## 1. Overview

REST API for the CMS section of the platform. Manages **Programs → Courses → Topics & Outcomes** used by the CMS UI.

- Base path: `/api/v1/cms`
- Format: JSON (`Content-Type: application/json`)
- Auth: Bearer token (JWT) on every request — `Authorization: Bearer <token>`
- Access control:
  - `ADMIN` — full access to CMS (and LMS when available)
  - `CONTENT_EDITOR` — full access to CMS only
  - Any other role → `403 Forbidden`

## 2. Entity Model

```
programs (1) ──< course (N)
course   (1) ──< topics (N)      -- ordered
course   (1) ──< outcomes (N)    -- ordered
```

| Entity  | Table    | PK           | FK                    |
|---------|----------|--------------|-----------------------|
| Program | programs | `program_id` | —                     |
| Course  | course   | `course_id`  | `program_id`          |
| Topic   | topics   | `topic_id`   | `course_id`           |
| Outcome | outcomes | `outcome_id` | `course_id`           |

Deletes cascade: program → courses → topics/outcomes.

---

## 3. Endpoints Summary

| # | Method | Path | Description |
|---|--------|------|-------------|
| 1 | GET    | `/programs`                                        | List programs (paginated, searchable) |
| 2 | POST   | `/programs`                                        | Create program |
| 3 | GET    | `/programs/{program_id}`                           | Get program (optionally with courses) |
| 4 | PUT    | `/programs/{program_id}`                           | Update program |
| 5 | DELETE | `/programs/{program_id}`                           | Delete program (cascades) |
| 6 | GET    | `/programs/{program_id}/courses`                   | List courses in a program |
| 7 | POST   | `/programs/{program_id}/courses`                   | Create course in a program |
| 8 | GET    | `/courses/{course_id}`                             | Get course (with topics + outcomes) |
| 9 | PUT    | `/courses/{course_id}`                             | Update course |
| 10 | DELETE | `/courses/{course_id}`                            | Delete course (cascades) |
| 11 | GET    | `/courses/{course_id}/topics`                     | List topics (ordered) |
| 12 | POST   | `/courses/{course_id}/topics`                     | Add topic (appends to end) |
| 13 | PUT    | `/topics/{topic_id}`                              | Update topic text |
| 14 | DELETE | `/topics/{topic_id}`                              | Delete topic (renumbers order) |
| 15 | PUT    | `/courses/{course_id}/topics/reorder`             | Reorder all topics |
| 16 | GET    | `/courses/{course_id}/outcomes`                   | List outcomes (ordered) |
| 17 | POST   | `/courses/{course_id}/outcomes`                   | Add outcome (appends to end) |
| 18 | PUT    | `/outcomes/{outcome_id}`                          | Update outcome text |
| 19 | DELETE | `/outcomes/{outcome_id}`                          | Delete outcome (renumbers order) |
| 20 | PUT    | `/courses/{course_id}/outcomes/reorder`           | Reorder all outcomes |

---

## 4. Programs

### 4.1 List programs

```
GET /api/v1/cms/programs
```

Query parameters:

| Param    | Type   | Default | Description |
|----------|--------|---------|-------------|
| `page`   | int    | 1       | Page number (1-based) |
| `size`   | int    | 20      | Page size (max 100) |
| `search` | string | —       | Case-insensitive match on `heading`, `sub_heading`, `university` |
| `country`| string | —       | Exact match filter |

Response `200 OK`:

```json
{
  "data": [
    {
      "program_id": 1,
      "heading": "MSc Data Science",
      "sub_heading": "Master of Science in Data Science",
      "short_description": "A postgraduate programme covering advanced analytics...",
      "country": "United Kingdom",
      "university": "University of Westford",
      "course_count": 3
    }
  ],
  "pagination": { "page": 1, "size": 20, "total_items": 1, "total_pages": 1 }
}
```

Note: `long_description` is omitted from the list response (payload size); fetch the single program for it. `course_count` is a computed field for the UI cards.

### 4.2 Create program

```
POST /api/v1/cms/programs
```

Request body:

```json
{
  "heading": "MSc Data Science",
  "sub_heading": "Master of Science in Data Science",
  "short_description": "...",
  "long_description": "...",
  "country": "United Kingdom",
  "university": "University of Westford"
}
```

Validation:

| Field               | Rules |
|---------------------|-------|
| `heading`           | required, 1–255 chars |
| `sub_heading`       | optional, ≤255 chars |
| `short_description` | optional |
| `long_description`  | optional |
| `country`           | optional, ≤100 chars |
| `university`        | optional, ≤255 chars |

Response `201 Created` — full program object including generated `program_id`.

### 4.3 Get program

```
GET /api/v1/cms/programs/{program_id}?include=courses
```

- Without `include`: program fields only.
- With `include=courses`: embeds the course list.

Response `200 OK`:

```json
{
  "program_id": 1,
  "heading": "MSc Data Science",
  "sub_heading": "Master of Science in Data Science",
  "short_description": "...",
  "long_description": "...",
  "country": "United Kingdom",
  "university": "University of Westford",
  "courses": [
    {
      "course_id": 1,
      "course_type": "Core Module",
      "heading": "Advanced Data Science",
      "period": "12 months",
      "delivery_method": "Online / Part-time",
      "fee": "£8,500",
      "topic_count": 7,
      "outcome_count": 3
    }
  ]
}
```

`404 Not Found` if the program does not exist.

### 4.4 Update program

```
PUT /api/v1/cms/programs/{program_id}
```

Body: same shape as create (full replace of editable fields). Same validation rules.
Response `200 OK` with the updated object. `404` if missing.

### 4.5 Delete program

```
DELETE /api/v1/cms/programs/{program_id}
```

Cascades to courses, topics, outcomes (DB `ON DELETE CASCADE`).
Response `204 No Content`. `404` if missing.

---

## 5. Courses

### 5.1 List courses in a program

```
GET /api/v1/cms/programs/{program_id}/courses
```

Response `200 OK`:

```json
{
  "data": [
    {
      "course_id": 1,
      "program_id": 1,
      "course_type": "Core Module",
      "heading": "Advanced Data Science",
      "description": "...",
      "period": "12 months",
      "delivery_method": "Online / Part-time",
      "fee": "£8,500",
      "progression_pathway": "PhD in Data Science...",
      "topic_count": 7,
      "outcome_count": 3
    }
  ]
}
```

### 5.2 Create course

```
POST /api/v1/cms/programs/{program_id}/courses
```

Request body:

```json
{
  "course_type": "Core Module",
  "heading": "Advanced Data Science",
  "description": "...",
  "period": "12 months",
  "delivery_method": "Online / Part-time",
  "fee": "£8,500",
  "progression_pathway": "PhD in Data Science, Lead Data Scientist roles"
}
```

Validation:

| Field                 | Rules |
|-----------------------|-------|
| `heading`             | required, 1–255 chars |
| `course_type`         | optional, ≤100 chars |
| `description`         | optional |
| `period`              | optional, ≤100 chars |
| `delivery_method`     | optional, ≤100 chars |
| `fee`                 | optional, ≤100 chars (free text, e.g. "£8,500") |
| `progression_pathway` | optional |

Response `201 Created` with the full course object. `404` if the program does not exist.

### 5.3 Get course

```
GET /api/v1/cms/courses/{course_id}
```

Always returns topics and outcomes embedded, ordered by `order` ascending:

```json
{
  "course_id": 1,
  "program_id": 1,
  "course_type": "Core Module",
  "heading": "Advanced Data Science",
  "description": "...",
  "period": "12 months",
  "delivery_method": "Online / Part-time",
  "fee": "£8,500",
  "progression_pathway": "...",
  "topics": [
    { "topic_id": 1, "order": 1, "topic": "Advanced Data Science" },
    { "topic_id": 2, "order": 2, "topic": "Statistical Modelling" }
  ],
  "outcomes": [
    { "outcome_id": 1, "order": 1, "outcome": "Lead Data Scientist" },
    { "outcome_id": 2, "order": 2, "outcome": "Data Engineering Manager" }
  ]
}
```

### 5.4 Update course

```
PUT /api/v1/cms/courses/{course_id}
```

Body: same shape/validation as create. Response `200 OK`. `404` if missing.

### 5.5 Delete course

```
DELETE /api/v1/cms/courses/{course_id}
```

Cascades to topics and outcomes. Response `204 No Content`.

---

## 6. Topics

Topics are an ordered list within a course. `order` is 1-based and contiguous (no gaps). The server owns renumbering.

### 6.1 List topics

```
GET /api/v1/cms/courses/{course_id}/topics
```

Response `200 OK`:

```json
{
  "data": [
    { "topic_id": 1, "course_id": 1, "order": 1, "topic": "Advanced Data Science" },
    { "topic_id": 2, "course_id": 1, "order": 2, "topic": "Statistical Modelling" }
  ]
}
```

### 6.2 Add topic

```
POST /api/v1/cms/courses/{course_id}/topics
```

Request body:

```json
{ "topic": "Deep Learning" }
```

Validation: `topic` required, 1–255 chars.
Behavior: server assigns `order = max(order) + 1` within the course (append to end).
Response `201 Created`:

```json
{ "topic_id": 8, "course_id": 1, "order": 8, "topic": "Deep Learning" }
```

### 6.3 Update topic text

```
PUT /api/v1/cms/topics/{topic_id}
```

Request body:

```json
{ "topic": "Deep Learning & Neural Networks" }
```

Only the text is editable here — `order` changes go through the reorder endpoint.
Response `200 OK` with the updated topic.

### 6.4 Delete topic

```
DELETE /api/v1/cms/topics/{topic_id}
```

Behavior: delete the row, then renumber remaining topics in the same course so `order` stays contiguous (all in one transaction).
Response `204 No Content`.

### 6.5 Reorder topics

```
PUT /api/v1/cms/courses/{course_id}/topics/reorder
```

Client sends the complete desired order as an array of topic IDs:

```json
{ "topic_ids": [3, 1, 2, 5, 4, 6, 7] }
```

Server rules:
- The array MUST contain exactly the set of topic IDs belonging to that course — no missing, no extra, no duplicates. Otherwise `400`.
- Server assigns `order = index + 1` in a single transaction.
- If the DB has `UNIQUE (course_id, "order")`, use deferred constraints or a two-phase update (set to negative temp values, then final values).

Response `200 OK` with the reordered list (same shape as 6.1).

---

## 7. Outcomes

Identical semantics to Topics (ordered list within a course).

### 7.1 List — `GET /courses/{course_id}/outcomes`
### 7.2 Add — `POST /courses/{course_id}/outcomes`

```json
{ "outcome": "ML Architect" }
```

Appends to end. `outcome` required, 1–255 chars.

### 7.3 Update — `PUT /outcomes/{outcome_id}`

```json
{ "outcome": "Machine Learning Architect" }
```

### 7.4 Delete — `DELETE /outcomes/{outcome_id}` (renumbers, `204`)
### 7.5 Reorder — `PUT /courses/{course_id}/outcomes/reorder`

```json
{ "outcome_ids": [2, 1, 3] }
```

Same rules as topic reorder.

---

## 8. Errors

Standard error envelope for all non-2xx responses:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "heading is required",
    "details": [
      { "field": "heading", "issue": "must not be blank" }
    ]
  }
}
```

| HTTP | Code                | When |
|------|---------------------|------|
| 400  | `VALIDATION_ERROR`  | Missing/invalid fields, bad reorder payload |
| 401  | `UNAUTHORIZED`      | Missing/expired/invalid token |
| 403  | `FORBIDDEN`         | Role has no CMS access |
| 404  | `NOT_FOUND`         | Program/course/topic/outcome does not exist |
| 409  | `CONFLICT`          | Concurrent modification (optional, if using optimistic locking) |
| 500  | `INTERNAL_ERROR`    | Unexpected server error |

`details` is optional and only present for validation errors.

---

## 9. Auth & Roles

- Every endpoint requires `Authorization: Bearer <JWT>`.
- The JWT must carry a role claim, e.g. `"role": "ADMIN"` or `"role": "CONTENT_EDITOR"`.
- Both roles have full CRUD on all CMS endpoints above.
- Role also drives the landing page: `ADMIN` sees CMS + LMS buttons, `CONTENT_EDITOR` sees CMS only. Expose this via the token claims or a `GET /api/v1/me` endpoint returning:

```json
{ "email": "editor@university.edu", "role": "CONTENT_EDITOR", "modules": ["CMS"] }
```

---

## 10. General Conventions

- All timestamps (if audit columns are added later) in ISO 8601 UTC: `2026-07-07T10:30:00Z`.
- IDs are server-generated integers (`SERIAL`); clients never supply them on create.
- `PUT` is full-replace of editable fields; fields omitted are treated as blank. (Switch to `PATCH` semantics later if partial updates are needed.)
- Pagination applies only to `GET /programs`; child collections are expected to be small and are returned in full.
- CORS: allow the CMS UI origin(s) only.
- Rate limiting and request size limits per platform standards.

## 11. Out of Scope (Phase 1)

- LMS endpoints
- Publishing/draft workflow (all content is live on save)
- Media/image upload for programs or courses
- Audit history / versioning
