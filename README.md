# Candidate Tracker API — Testing, Docker & Deployment

![Tests](https://github.com/Estherry-techstar/week5-docker-deploy/actions/workflows/test.yml/badge.svg)

A CRUD API for tracking job candidates, backed by PostgreSQL, protected by JWT authentication.

This builds on the Week 3 PostgreSQL migration. The shared API key has been replaced with per-user accounts, signed tokens, rate limiting, and non-disclosing error responses.

Week 5 adds a hardened container build, a one-command local stack, coverage for expired tokens and database outages, and documented deployment to Azure Container Apps.

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
git clone https://github.com/Estherry-techstar/week5-docker-deploy.git
cd week5-docker-deploy

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

**All auth failures return one identical 401.** Expired token, forged signature, deleted user, deactivated account — the same message. Distinct messages would tell an attacker which part of their guess was right. `test_expired_token_looks_like_any_other_bad_token` asserts the expired and forged cases are byte-for-byte identical.

**Tokens carry only `sub` and `exp`.** A JWT is signed, not encrypted; anyone holding it can base64-decode the payload. Only the user id and expiry go in.

**Every request loads the user from the database.** A self-contained token needs no lookup, but then it cannot be revoked before expiry. The lookup — one indexed primary-key read — is what makes `is_active = false` take effect immediately. This is tested in `test_token_for_deactivated_user_is_rejected`.

**Rate limiting is a first layer, not a complete defence.** slowapi keys on client IP, so it does not stop a distributed attack, and behind a proxy every request appears to come from the proxy unless `X-Forwarded-For` is handled. Production would add per-account limits and exponential backoff.

**No secret has a fallback default.** `JWT_SECRET_KEY` and the database credentials are declared in `app/config.py` without default values, so a missing variable stops the process at import with a validation error. An earlier version defaulted the signing key to a hardcoded development string, which meant a misconfigured deployment would run and sign tokens with a key visible in the repository.

**A database outage returns 503, not a traceback.** SQLAlchemy puts the connection string — host, user, database — into its error messages. The handler in `main.py` logs the detail server-side and returns a generic body, so an outage does not become an information leak. 503 also tells clients and load balancers that retrying is worthwhile, which 500 does not.

## Configuration

`.env` is gitignored; `.env.example` documents the required keys.

| Variable | Purpose |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | database credentials |
| `POSTGRES_HOST` | `db` inside Docker, `localhost` on the host |
| `POSTGRES_PORT` | `5433` on the host, `5432` inside Docker |
| `POSTGRES_SSLMODE` | `prefer` locally, `require` against managed Postgres |
| `JWT_SECRET_KEY` | signs and verifies tokens — the most sensitive value here |
| `JWT_ALGORITHM` | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | token lifetime, default 30 |

All of these are read through a single `Settings` class in `app/config.py` rather than scattered `os.getenv` calls, so the full set of required configuration is visible in one place and validated at startup.

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"` — `secrets` uses the OS cryptographic source; `random` is predictable and unsuitable.

The CI workflow uses a plaintext secret because it signs tokens for a throwaway database destroyed after each run. A real deployment would use GitHub encrypted secrets.

## Container build

`docker compose up -d --build` brings up the API and database together. The Postgres init script in `db-init/` creates the test database on first run, so a clean checkout needs no manual setup before `pytest`.

The image is built in two stages: dependencies compile into a virtualenv in the builder stage, and only the finished virtualenv is copied into the runtime image, leaving build tooling behind. `requirements.txt` is copied and installed before the source, so editing application code reuses the cached dependency layer instead of reinstalling everything.

The container runs as `appuser` (uid 1000), not root. `PYTHONUNBUFFERED=1` keeps logs flowing to `docker logs` rather than sitting in Python's stdout buffer.

The API service waits on the database's healthcheck rather than starting immediately, then runs `alembic upgrade head` before uvicorn, so a fresh volume gets its schema without intervention.

Deployment to Azure Container Apps is documented end to end in [DEPLOYMENT.md](DEPLOYMENT.md), including the secrets handling, verification steps, rollback procedure, and the trade-offs that were accepted rather than solved.

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
| `tests/test_failures.py` | Expired tokens, database outages |

Each test runs in a transaction that is rolled back on teardown, so every test starts from an empty database. An `autouse` fixture resets the rate limiter between tests, since its state is global.

84 tests, 96% statement coverage (`pytest --cov=app --cov-report=term-missing`). The uncovered lines are `get_db` itself, which every test overrides, and the concurrent-duplicate-signup branch, which needs two simultaneous requests to reach.

Two failure modes get dedicated coverage because they are hard to trigger by accident. **Expired tokens** are built with an `exp` already in the past — correctly signed, so only the expiry distinguishes them from a valid token; a companion test asserts a token expiring in 30 seconds is still accepted, guarding against an off-by-one that would reject valid tokens. **Database outages** are simulated by overriding the `get_db` dependency with one that raises `OperationalError`, which exercises the failure path without stopping Postgres.

CI runs migrations forward, backward, and forward again against a clean PostgreSQL service, then runs the suite.

## Known limitations

- **Signup gives the legitimate user no confirmation.** Because the response is uniform, someone who mistypes an existing address gets the same 202 as a successful registration. The standard fix is to move the real signal to the address itself: a confirmation email for new registrations, a "someone tried to register your address" notice with a reset link for duplicates. That needs a mail layer, which this project does not have yet.
- No refresh tokens. When a 30-minute token expires the user logs in again.
- No password reset or change flow.
- No roles or permissions — every authenticated user has identical access.
- Interviews still have no HTTP endpoints.
- Rate limit state is in-memory, so it resets on restart and is not shared across replicas. Redis would fix both.
- Migrations run in the container's startup command. That is safe at one replica and breaks at more than one, since each replica would attempt the migration concurrently. Moving them to a one-shot job is a prerequisite for scaling.
- Deployment is manual. CI runs the test suite but does not deploy, so releases are repeatable only by following DEPLOYMENT.md rather than automatically.
- The deployed database firewall admits any Azure-internal address rather than using VNet private access. See DEPLOYMENT.md §5.2 for the reasoning and §11 for the full list of deployment trade-offs.
