# CMS API — User Management UI Integration Reference

Base path: `/api/v1/cms` · JSON in/out · No auth header required yet (auth middleware not implemented).

This section has no separate "role" concept — each user simply holds a set of
`access` grants. Frontend currently talks to an in-memory mock
(`src/api/users.js`) with this exact contract; swapping in the real backend
should be a drop-in change to that one file.

## Error shape (any non-2xx)

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "...", "details": [{ "field": "email", "issue": "..." }] } }
```

| Code | HTTP | When |
|---|---|---|
| `VALIDATION_ERROR` | 400 | bad/missing fields, invalid `access` value |
| `NOT_FOUND` | 404 | user id doesn't exist |
| `CONFLICT` | 409 | email already in use by another user |

`details` is only present for validation errors.

---

## Users

### `UserListItem` / `UserDetail` shape
```json
{
  "user_id": 1,
  "name": "Ashan Perera",
  "email": "ashan.perera@inspire.edu",
  "access": ["USER_MANAGEMENT", "CMS"]
}
```
- `access` is an array of zero or more of: `USER_MANAGEMENT`, `CMS`, `LMS`. Empty array = no access granted.
- List and detail responses use the same shape (no separate summary vs. detail fields needed for this resource).

### List — `GET /users`
- No paging/search yet — the UI renders the whole list.
- **200**: `{ "data": [UserListItem] }`

### Create — `POST /users`
- Body: `name` (required, 1–255), `email` (required, valid email, ≤255, unique), `access` (optional array, values restricted to the three enum values above, defaults to `[]`).
- **201**: `UserDetail`
- **400** `VALIDATION_ERROR` if `name`/`email` missing or `access` contains an unknown value.
- **409** `CONFLICT` if `email` is already taken.

### Get — `GET /users/{user_id}`
- **200**: `UserDetail`. **404** if missing.

### Update — `PUT /users/{user_id}`
- Body: same as Create (full replace of `name`, `email`, `access`).
- **200**: `UserDetail`. **404** if missing. **400**/**409** same rules as Create.

### Delete — `DELETE /users/{user_id}`
- **204**. Revokes all access immediately. **404** if missing.

---

## Not in scope (per current UI)
- No password/authentication fields — login is a separate concern not modeled by this section yet.
- No roles, groups, or per-module permission levels — `access` is a flat set of module keys.
