# News & Events API

News and event content is managed through authenticated CMS endpoints and displayed on the public Main UI through read-only endpoints.

## Database setup

Run once from the `inspire-api` folder:

```powershell
.\.venv\Scripts\python.exe scripts\apply_news_events_migration.py
```

The migration is idempotent. It creates `news_events` and imports the five bundled website news items when their slugs do not already exist.

## CMS endpoints

Base URL: `/api/v1/cms/news-events`

All CMS routes require `Authorization: Bearer <access-token>` and CMS access.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/cms/news-events` | List and filter all items |
| `POST` | `/api/v1/cms/news-events` | Create an item |
| `GET` | `/api/v1/cms/news-events/{id}` | Get one item |
| `PUT` | `/api/v1/cms/news-events/{id}` | Replace an item |
| `DELETE` | `/api/v1/cms/news-events/{id}` | Delete an item |

List filters: `page`, `size`, `search`, `status`, `kind`, and `category`.

### Create/update example

```json
{
  "slug": "inspire-open-day-2026",
  "title": "Inspire College Open Day 2026",
  "kind": "Event",
  "category": "Open Day",
  "image_url": "https://example.com/open-day.jpg",
  "excerpt": "Meet the faculty and explore our online programmes.",
  "content": [
    "Join our online Open Day for a guided platform tour.",
    "An admissions advisor will send the meeting link after registration."
  ],
  "author": "Admissions Team",
  "status": "Published",
  "published_on": "2026-08-13",
  "event_date": "2026-09-05"
}
```

When an item is saved as `Published` without `published_on`, the API uses the current date.

## Public endpoints

These routes do not require authentication and return only `Published` items.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/public/news-events` | Published items, newest first |
| `GET` | `/api/v1/public/news-events/{slug}` | One published item |

The public list accepts `limit`, `kind`, and `category`. Draft and Review items are never returned.
