# Candidate Tracker API — JWT Authentication

![Tests](https://github.com/Estherry-techstar/week4-jwt-auth/actions/workflows/test.yml/badge.svg)

A CRUD API for tracking job candidates, backed by PostgreSQL, protected by JWT authentication.

This builds on the Week 3 PostgreSQL migration. The shared API key has been replaced with per-user accounts, signed tokens, rate limiting, and non-disclosing error responses.

## Stack

- **FastAPI** — HTTP layer
- **PostgreSQL 16** — database, via Docker Compose
- **SQLAlchemy 2.0 / Alembic** — ORM and migrations
- **bcrypt** — password hashing
- **python-jose** — JWT signing and verification
- **slowapi** — rate limiting
- **pytest** — full suite against a real database

## Quick start

```bash
git clone https://github.com/Estherry-techstar/week4-jwt-auth.git
cd week4-jwt-auth

cp .env.example .env          # Windows: copy .env.example .env
# then set JWT_SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose up -d --build
```

Open http://localhost:8001/docs

1. `POST /auth/signup` with an email and password. The response is always a generic 202 — see [Security decisions](#security-decisions) for why it does not confirm whether the account was created.
2. Click **Authorize**, enter the same credentials
3. All `/candidates` endpoints now work

## Authentication flow

```
signup ──▶ password hashed with bcrypt ──▶ stored (never the plaintext)

login  ──▶ password verified ──▶ signed JWT returned
                                       │
                                       ▼
request ──▶ Authorization: Bearer <token>
                    │
                    ▼
            signature + expiry checked
                    │
                    ▼
            user loaded, is_active checked ──▶ endpoint runs
```

| Endpoint | Auth | Rate limit |
|---|---|---|
| `GET /health` | none | none |
| `POST /auth/signup` | none | 5/hour |
| `POST /auth/login` | none | 5/minute |
| `GET /auth/me` | JWT | none |
| `POST /candidates` | JWT | none |
| `GET /candidates` | JWT | none |
| `GET /candidates/{id}` | JWT | none |
| `PATCH /candidates/{id}` | JWT | none |
| `DELETE /candidates/{id}` | JWT | none |

## Security decisions

**bcrypt, not a general-purpose hash.** bcrypt is deliberately slow and salts automatically, so identical passwords produce different hashes and a stolen database is expensive to crack. MD5 or SHA-256 would be fast, which is exactly wrong for passwords.

**Passwords capped at 72 bytes.** bcrypt silently ignores anything beyond 72 bytes, so `"a"*100` and `"a"*200` would hash identically. bcrypt 5.0 raises rather than truncating, which would surface as a 500. The cap sits on the Pydantic schema, so it returns a clean 422 instead.

**Login is timing-safe.** bcrypt verification takes ~250ms. Returning early when an email isn't found would make unknown emails respond in ~2ms and known emails in ~250ms — a measurable difference that turns login into an account-enumeration oracle. `authenticate_user` runs a dummy verification against a pre-computed hash when the email is unknown, so both paths take the same time. The `is_active` check happens after verification for the same reason.

**Signup does not disclose whether an email is registered.** `POST /auth/signup` returns 202 with an identical body whether the address is new or already taken, and the duplicate branch runs the same dummy verification as login so latency does not leak what the status code no longer does. Returning 409 on a duplicate would let anyone test a list of addresses against the user table, and for a KYC or financial client, membership in that table is itself disclosure — it answers "does this person hold an account here?".

The duplicate branch never touches the existing row. Quietly overwriting the stored hash to make the response look uniform would turn signup into account takeover. `test_duplicate_signup_does_not_change_the_existing_password` covers this.

The existence check and the insert are separate statements, so two concurrent signups for the same address can both pass the check. The insert is wrapped in an `IntegrityError` handler that rolls back and returns the same generic 202, rather than surfacing a 500 that would be a rarer version of the same leak.

**All auth failures return one identical 401.** Expired token, forged signature, deleted user, deactivated account — the same message. Distinct messages would tell an attacker which part of their guess was right.

**Tokens carry only `sub` and `exp`.** A JWT is signed, not encrypted; anyone holding it can base64-decode the payload. Only the user id and expiry go in.

**Every request loads the user from the database.** A self-contained token needs no lookup, but then it cannot be revoked before expiry. The lookup — one indexed primary-key read — is what makes `is_active = false` take effect immediately. This is tested in `test_token_for_deactivated_user_is_rejected`.

**Rate limiting is a first layer, not a complete defence.** slowapi keys on client IP, so it does not stop a distributed attack, and behind a proxy every request appears to come from the proxy unless `X-Forwarded-For` is handled. Production would add per-account limits and exponential backoff.

## Configuration

`.env` is gitignored; `.env.example` documents the required keys.

| Variable | Purpose |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | database credentials |
| `POSTGRES_HOST` | `db` inside Docker, `localhost` on the host |
| `POSTGRES_PORT` | `5433` on the host, `5432` inside Docker |
| `JWT_SECRET_KEY` | signs and verifies tokens — the most sensitive value here |
| `JWT_ALGORITHM` | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | token lifetime, default 30 |

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"` — `secrets` uses the OS cryptographic source; `random` is predictable and unsuitable.

The CI workflow uses a plaintext secret because it signs tokens for a throwaway database destroyed after each run. A real deployment would use GitHub encrypted secrets.

## Data model

Three tables. `candidates` and `interviews` carry over from Week 3 in a one-to-many relationship; `users` is new.

```
users                      candidates ──< interviews
-----                      ----------     ----------
id (PK)                    id (PK)        id (PK)
email (UNIQUE, indexed)    ...            candidate_id (FK, CASCADE)
hashed_password (60)                      ...
is_active
created_at
```

`hashed_password` is `String(60)` because a bcrypt hash is always exactly 60 characters. `users` has no relationship to `candidates` — a user operates the tracker, they are not tracked by it.

## Tests

The suite runs against a real PostgreSQL instance.

```bash
docker compose up -d db
pytest -v
```

| File | Covers |
|---|---|
| `tests/test_auth.py` | Hashing, salting, token round-trip, tampered tokens, signup, login, revocation, rate limiting |
| `tests/test_api.py` | Candidate endpoints, all status codes, JWT enforcement |
| `tests/test_crud.py` | Data layer, constraints, CASCADE |

Each test runs in a transaction that is rolled back on teardown, so every test starts from an empty database. An `autouse` fixture resets the rate limiter between tests, since its state is global.

CI runs migrations forward, backward, and forward again against a clean PostgreSQL service, then runs the suite.

## Known limitations

- **Signup gives the legitimate user no confirmation.** Because the response is uniform, someone who mistypes an existing address gets the same 202 as a successful registration. The standard fix is to move the real signal to the address itself: a confirmation email for new registrations, a "someone tried to register your address" notice with a reset link for duplicates. That needs a mail layer, which this project does not have yet.
- No refresh tokens. When a 30-minute token expires the user logs in again.
- No password reset or change flow.
- No roles or permissions — every authenticated user has identical access.
- Interviews still have no HTTP endpoints.
- Rate limit state is in-memory, so it resets on restart and is not shared across replicas. Redis would fix both.