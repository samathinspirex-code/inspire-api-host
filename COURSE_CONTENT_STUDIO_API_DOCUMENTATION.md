# Course Content Studio API

Course Content Studio extends the existing `Course -> Module` hierarchy. A module is presented as an ordered weekly section, and each section contains ordered learning items.

## Database setup

```powershell
.\.venv\Scripts\python.exe scripts\apply_course_content_studio_migration.py
```

For an IPv4-only network with Supabase, configure the **Session pooler** connection from the Supabase Connect panel (port `5432`) instead of the IPv6-only direct database host.

## Student and lecturer view

| Method | Endpoint | Roles | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/lms/my/courses/{course_id}/studio` | Lecturer, Student | Ordered sections, published resources, and resolved access state |

Locked student responses retain item titles but omit `resource_url` and `text_content`.

## Lecturer section management

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/lms/studio/courses/{course_id}/sections` | Create a section |
| `PUT` | `/api/v1/lms/studio/sections/{module_id}` | Edit/publish a section |
| `DELETE` | `/api/v1/lms/studio/sections/{module_id}` | Delete a section and its items |
| `PUT` | `/api/v1/lms/studio/courses/{course_id}/sections/reorder` | Reorder every course section |

## Learning items

Supported types are `video`, `pdf`, `text`, `link`, `assignment`, and `quiz`.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/lms/studio/sections/{module_id}/items` | Add learning content |
| `PUT` | `/api/v1/lms/studio/items/{item_id}` | Edit or publish content |
| `DELETE` | `/api/v1/lms/studio/items/{item_id}` | Delete content |
| `PUT` | `/api/v1/lms/studio/sections/{module_id}/items/reorder` | Reorder every item in a section |

Example video item:

```json
{
  "item_type": "video",
  "title": "Introduction to programming",
  "description": "Watch before the live class.",
  "resource_url": "https://vimeo.com/123456789",
  "text_content": null,
  "duration_minutes": 38,
  "status": "published",
  "is_required": true
}
```

## Release rules

`PUT /api/v1/lms/studio/sections/{module_id}/access`

The `scope_type` can be `course`, `class`, or `student`. `available_from` may be omitted for immediate access.

```json
{
  "scope_type": "class",
  "scope_id": 12,
  "is_unlocked": true,
  "available_from": "2026-08-20T08:30:00+05:30"
}
```

Individual student rules take precedence over class rules, and class rules take precedence over the whole-course rule.

## Course discussions

Only lecturers assigned to the course and actively enrolled students can read or post messages.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/lms/my/courses/{course_id}/discussions` | List the course community messages in chronological order |
| `POST` | `/api/v1/lms/my/courses/{course_id}/discussions` | Post a lecturer or student message |

```json
{
  "message": "Please complete Week 1 before our live class."
}
```
