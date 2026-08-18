# Auth Flow — Implementation Instructions

Authentication service specification: passwordless email OTP login using **Resend** for email delivery, PostgreSQL for OTP storage, and JWT-based sessions with access levels.

Scope: authentication and access control only. CMS content APIs and user-management APIs are documented in separate files.

## 1. Overview

```
Client (React UI)
   │ 1. email                          ┌──────────────┐
   ├──────────────────────────────────►│ request-otp  │
   │                                   └──────┬───────┘
   │                                          │ user exists & active?
   │                                          ▼
   │                                   generate 6-digit OTP
   │                                   upsert SHA-256 hash into login_otps (5-min expiry)
   │                                   send OTP email via Resend
   │ 2. 200 "code sent" (always)              │
   │◄─────────────────────────────────────────┘
   │
   │ 3. email + otp                    ┌──────────────┐
   ├──────────────────────────────────►│ verify-otp   │
   │                                   └──────┬───────┘
   │                                          │ hash match + not expired + attempts < 5?
   │                                          ▼
   │ 4. access token (JWT, 15 min)     delete OTP (single use)
   │    + refresh token (30 days)      load user's access levels
   │◄─────────────────────────────────────────┘
   │
   ▼
Authenticated API calls: Authorization: Bearer <access_token>
```

Key decisions:

- **No passwords.** Login = email + OTP only.
- **No self-registration.** Users are pre-created by an admin. Unknown emails receive no OTP but get the same success response (prevents user enumeration).
- **Access levels** (`CMS`, `LMS`, `USER_MANAGEMENT`) are assigned directly to users and embedded in the JWT — downstream services authorize from the token claims.
- **Resend** is the email provider. The API token is already generated — read it from environment config (`RESEND_API_KEY`); never hardcode it.

## 2. DDL (PostgreSQL)

```sql
-- Users (passwordless — no password column)
CREATE TABLE users (
    user_id     SERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL UNIQUE,
    full_name   VARCHAR(255),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  INT REFERENCES users(user_id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive login lookup
CREATE UNIQUE INDEX idx_users_email_lower ON users (LOWER(email));

-- Access levels (capabilities)
CREATE TABLE access_levels (
    access_level_id  SERIAL PRIMARY KEY,
    access_key       VARCHAR(100) NOT NULL UNIQUE,   -- checked by code, never renamed
    display_name     VARCHAR(255) NOT NULL,          -- shown in admin UI
    description      TEXT,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- User ↔ access level mapping
CREATE TABLE user_access_levels (
    user_id          INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    access_level_id  INT NOT NULL REFERENCES access_levels(access_level_id) ON DELETE CASCADE,
    assigned_by      INT REFERENCES users(user_id),
    assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, access_level_id)
);

CREATE INDEX idx_ual_access_level_id ON user_access_levels(access_level_id);

-- Refresh tokens (revocable sessions; store hash only, never the token)
CREATE TABLE refresh_tokens (
    token_id    SERIAL PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL UNIQUE,          -- SHA-256 hex
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens(user_id);

-- Login OTPs — one active OTP per email (new request overwrites the old one).
-- Email is stored lowercased (application normalizes before insert).
CREATE TABLE login_otps (
    otp_id        SERIAL PRIMARY KEY,
    email         VARCHAR(255) NOT NULL UNIQUE,        -- lowercased
    otp_hash      VARCHAR(64)  NOT NULL,               -- SHA-256 hex, never plaintext
    attempts      INT NOT NULL DEFAULT 0,              -- failed verify counter
    expires_at    TIMESTAMPTZ NOT NULL,                -- now() + 5 minutes
    last_sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- resend cooldown check
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

> Because each email has at most one row (upsert on request), `login_otps` stays tiny. Expired rows are overwritten on the next request; an optional daily cleanup job can purge stale rows (section 10).

### Seed data

```sql
INSERT INTO access_levels (access_key, display_name, description) VALUES
('CMS',             'CMS Access',      'Manage programs, courses, topics and outcomes'),
('LMS',             'LMS Access',      'Learning management (Phase 2 — not yet available)'),
('USER_MANAGEMENT', 'User Management', 'Create users and assign access levels');

-- Bootstrap first admin (replace email before running).
-- Required: there is no self-registration — without this nobody can log in.
INSERT INTO users (email, full_name) VALUES ('admin@yourcompany.com', 'System Admin');

INSERT INTO user_access_levels (user_id, access_level_id, assigned_by)
SELECT u.user_id, al.access_level_id, u.user_id
FROM users u, access_levels al
WHERE u.email = 'admin@yourcompany.com';
```

## 3. OTP Rules

| Rule | Value |
|------|-------|
| Format | 6 digits, cryptographically random (`SecureRandom` / `secrets`) — never `Math.random` |
| Storage | SHA-256 hash in `login_otps`, never plaintext |
| Expiry | 5 minutes |
| Max verify attempts | 5 — then invalidate OTP, user must request a new one |
| Single use | Deleted immediately on successful verification |
| Resend cooldown | 60 seconds per email → `429` if violated |
| New OTP | Overwrites/invalidates any previous OTP for that email |
| IP rate limit | 10 `request-otp` calls per hour per IP |
| Response uniformity | Identical `200` response for known and unknown emails |
| Email normalization | Lowercase + trim before lookup and key construction |

## 4. OTP Storage & Logic (PostgreSQL)

One row per email in `login_otps`; a new request overwrites the previous OTP via upsert.

**Request-otp:**

1. Cooldown check — if a row exists with `last_sent_at > now() - interval '60 seconds'` → `429 OTP_COOLDOWN`.
2. Upsert the new OTP (resets attempts, extends expiry):

```sql
INSERT INTO login_otps (email, otp_hash, attempts, expires_at, last_sent_at)
VALUES ($1, $2, 0, now() + interval '5 minutes', now())
ON CONFLICT (email) DO UPDATE
SET otp_hash     = EXCLUDED.otp_hash,
    attempts     = 0,
    expires_at   = EXCLUDED.expires_at,
    last_sent_at = now();
```

3. Send email via Resend.

**Verify-otp:**

1. Fetch the row:

```sql
SELECT otp_hash, attempts, expires_at
FROM login_otps
WHERE email = $1;
```

2. No row, or `expires_at <= now()` → `401 OTP_EXPIRED` (delete the expired row if present).
3. `attempts >= 5` → `401 OTP_MAX_ATTEMPTS`.
4. Compare `sha256(submitted)` with `otp_hash`:
   - Mismatch → `UPDATE login_otps SET attempts = attempts + 1 WHERE email = $1;` — if this makes attempts 5, return `401 OTP_MAX_ATTEMPTS`, otherwise `401 OTP_INVALID`.
   - Match → `DELETE FROM login_otps WHERE email = $1;` (single use) → issue tokens.

Steps 1–4 on the failure path should run in a single transaction (`SELECT ... FOR UPDATE`) so parallel verify attempts can't bypass the attempt counter.

## 5. Email Delivery — Resend

Use the **Resend** API (`https://api.resend.com/emails`) with the already-generated API token.

```
POST https://api.resend.com/emails
Authorization: Bearer ${RESEND_API_KEY}
Content-Type: application/json

{
  "from": "Course Studio <no-reply@yourdomain.com>",
  "to": ["user@company.com"],
  "subject": "Your login code",
  "html": "<p>Your verification code is:</p><h2>483920</h2><p>It expires in 5 minutes. If you didn't request this, ignore this email.</p>"
}
```

Requirements:
- `RESEND_API_KEY` from environment/secret config — never committed or logged.
- The `from` domain must be verified in the Resend dashboard before sending works.
- Send asynchronously (don't block the `request-otp` response on the Resend call) but log delivery failures with the Resend message id / error.
- Email content: the code, the 5-minute expiry, and an "ignore if you didn't request this" line. Never include the code in the subject line's tracking-friendly parts or in any logs.
- A Resend failure must not reveal anything to the client — response stays the uniform `200`.

## 6. Tokens & Sessions

| Token | Lifetime | Format | Storage |
|-------|----------|--------|---------|
| Access token | 15 minutes | JWT, RS256 | Client memory |
| Refresh token | 30 days | Opaque random 256-bit | Client (httpOnly cookie or secure storage); server stores SHA-256 hash in `refresh_tokens` |

JWT claims:

```json
{
  "sub": "1",
  "email": "user@company.com",
  "access": ["CMS", "USER_MANAGEMENT"],
  "iat": 1751980000,
  "exp": 1751980900
}
```

- **RS256**: private key signs in the auth service; public key is distributed to other services for validation. Keys stored in AWS Parameter Store.
- **Refresh rotation**: every `POST /auth/refresh` revokes the presented token (`revoked_at = now()`) and issues a new pair. Presenting an already-revoked token is treated as theft → revoke **all** refresh tokens for that user.
- **Propagation**: access-level changes take effect at access-token expiry (≤15 min). Deactivating a user should also revoke their refresh tokens immediately.

## 7. API Endpoints

Base: `/api/v1/auth` (all public unless noted). All requests/responses JSON.

### 7.1 `POST /api/v1/auth/request-otp`

Request:
```json
{ "email": "user@company.com" }
```

Validation: `email` required, valid format.

Behavior:
1. Normalize email (lowercase, trim).
2. Look up user — must exist and `is_active = true` to actually send. Either way, respond identically.
3. Apply cooldown + rate limits, then generate/store/send per sections 3–5.

Response `200 OK` (always):
```json
{ "message": "If the account exists, a verification code has been sent." }
```

`429`:
```json
{ "error": { "code": "OTP_COOLDOWN", "message": "Please wait before requesting another code." } }
```

### 7.2 `POST /api/v1/auth/verify-otp`

Request:
```json
{ "email": "user@company.com", "otp": "483920" }
```

Validation: both required; `otp` exactly 6 digits.

Response `200 OK`:
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque>",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "user_id": 1,
    "email": "user@company.com",
    "full_name": "System Admin",
    "access": ["CMS", "USER_MANAGEMENT"]
  }
}
```

Errors (`401`):

| Code | When |
|------|------|
| `OTP_INVALID` | Wrong code (attempts remaining) |
| `OTP_EXPIRED` | No active OTP for this email (expired or never requested) |
| `OTP_MAX_ATTEMPTS` | 5 failures — OTP invalidated, request a new one |

### 7.3 `POST /api/v1/auth/refresh`

Request:
```json
{ "refresh_token": "<opaque>" }
```

Behavior: hash token → find row → must be unexpired and not revoked → revoke it, insert new one, issue new access token.

Response `200 OK`: same shape as verify-otp.

`401 REFRESH_INVALID` if unknown/expired/revoked. If revoked (reuse) → revoke all sessions for that user before responding.

### 7.4 `POST /api/v1/auth/logout` (authenticated)

Request:
```json
{ "refresh_token": "<opaque>" }
```

Revokes that refresh token. Response `204 No Content`.

### 7.5 `GET /api/v1/me` (authenticated)

Returns the current user from the token (used by the UI to decide which module buttons to show):

```json
{
  "user_id": 1,
  "email": "user@company.com",
  "full_name": "System Admin",
  "access": ["CMS", "USER_MANAGEMENT"]
}
```

`401 UNAUTHORIZED` if token missing/invalid/expired.

## 8. Authorization Middleware (for downstream endpoints)

- Every protected endpoint declares its required access key (e.g. CMS endpoints require `CMS`; user-management endpoints require `USER_MANAGEMENT` — specs in their own files).
- Middleware validates the JWT signature (RS256 public key), expiry, then checks the `access` claim contains the required key.
- Missing/invalid/expired token → `401 UNAUTHORIZED`. Valid token without the required access key → `403 FORBIDDEN`.
- Frontend button visibility from `/me` is cosmetic only — the middleware is the real gate.

## 9. Error Format

All non-2xx responses use one envelope:

```json
{
  "error": {
    "code": "OTP_INVALID",
    "message": "The code entered is incorrect."
  }
}
```

| HTTP | Codes |
|------|-------|
| 400 | `VALIDATION_ERROR` |
| 401 | `UNAUTHORIZED`, `OTP_INVALID`, `OTP_EXPIRED`, `OTP_MAX_ATTEMPTS`, `REFRESH_INVALID` |
| 403 | `FORBIDDEN` |
| 429 | `OTP_COOLDOWN`, `RATE_LIMITED` |
| 500 | `INTERNAL_ERROR` |

## 10. Security & Operational Notes

- **Never log**: OTP values, tokens (access or refresh), or the Resend API key. Log auth events (OTP requested / verified / failed, refresh, logout) with user_id and timestamp.
- **Config via environment/secrets**: `RESEND_API_KEY`, database connection, JWT key references (Parameter Store paths), rate-limit values.
- **Cleanup (optional)**: daily job `DELETE FROM login_otps WHERE expires_at < now() - interval '1 day';` — not strictly required since rows are overwritten per email, but keeps the table free of stale entries for users who never completed login.
- **CORS**: allow only the CMS UI origin(s).
- **Transport**: HTTPS only; refresh token cookie (if used) must be `httpOnly`, `Secure`, `SameSite=Strict`.
- **Clock**: OTP and token expiry comparisons in UTC server time.
- **Health**: `GET /health` (public, no auth) for load balancer checks.
