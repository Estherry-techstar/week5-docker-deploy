# Candidate Tracker API

![Tests](https://github.com/Estherry-techstar/week3-postgres-api/actions/workflows/test.yml/badge.svg)

A CRUD API for tracking job candidates, backed by PostgreSQL with SQLAlchemy and Alembic migrations.

This is a migration of an earlier in-memory version. The HTTP contract is unchanged; only the storage layer was replaced.

## Stack

- **FastAPI** — HTTP layer
- **PostgreSQL 16** — database, run via Docker Compose
- **SQLAlchemy 2.0** — ORM
- **Alembic** — schema migrations
- **Pydantic** — request/response validation
- **pytest** — 48 tests against a real PostgreSQL instance

## Quick start

Requires Docker Desktop only.

```bash
git clone https://github.com/Estherry-techstar/week3-postgres-api.git
cd week3-postgres-api

cp .env.example .env          # Windows: copy .env.example .env

docker compose up -d --build  # starts Postgres and the API, runs migrations
docker compose exec api python seed.py
```

Open http://localhost:8000/docs

Authorize with the `X-API-Key` header using the value of `API_KEY` from your `.env` (default: `dev-secret-key`).

Migrations run automatically on API startup, and the API waits for PostgreSQL's healthcheck before booting.

### Running the API outside Docker

To run the API locally against the containerised database — useful during development, since `--reload` picks up code changes:

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d db       # database only
alembic upgrade head
python seed.py
uvicorn app.main:app --reload
```

Set `POSTGRES_HOST=localhost` in `.env` for this mode. Inside Docker the host is `db`, the Compose service name.

## Data model

Two related tables in a one-to-many relationship.

```
candidates                        interviews
----------                        ----------
id           PK                   id            PK
full_name                         candidate_id  FK → candidates.id  ON DELETE CASCADE
email        UNIQUE, indexed      scheduled_at
role                              interviewer
years_experience                  feedback
stage        ENUM
notes
created_at   default now()
updated_at   default now(), onupdate now()
```

A candidate has many interviews. The foreign key lives on `interviews` — the "many" side — because a relational column holds a single scalar value.

### Design decisions

**`ON DELETE CASCADE`** — an interview without a candidate is meaningless, so deleting a candidate removes their interviews atomically. This keeps `DELETE /candidates/{id}` returning 204 unconditionally, as it did before the migration. `RESTRICT` would have forced a contract change.

**UNIQUE constraint on `email`** — previously enforced by a Python loop over a dict. The API still returns 409 with a friendly message, but the database now guarantees correctness even if application code is wrong.

**Timestamps set by the database** — `server_default=func.now()` and `onupdate=func.now()` replace a manual `utcnow()` helper, so rows inserted by seed scripts or raw SQL get correct timestamps too. `timezone=True` preserves the timezone-aware datetimes the original API returned.

**`stage` as a Postgres ENUM** — invalid stages are rejected by the database, not only by Pydantic.

## What moved from application code into the database

| Previously in Python | Now in PostgreSQL |
|---|---|
| `count(1)` id counter | `SERIAL` sequence |
| `email_exists()` loop | `UNIQUE` constraint + index |
| `utcnow()` set by hand | `DEFAULT now()` / `ON UPDATE` |
| List slicing for pagination | `LIMIT` / `OFFSET` |
| `needle in name.lower()` | `ILIKE` |
| Manual cleanup of related rows | `ON DELETE CASCADE` |
| Data lost on restart | Rows on disk in a named volume |

Filtering and pagination now execute in the database rather than loading every row into Python and discarding most of them.

## Tests

48 tests run against a real PostgreSQL instance — not mocks, not SQLite.

```bash
docker compose up -d db
pytest -v
```

`tests/conftest.py` provides an isolated `appdb_test` database. Each test runs inside a transaction that is rolled back on teardown, so tests cannot affect one another and every test starts from an empty table. This is also why the suite completes in about a second.

| File | Covers |
|---|---|
| `tests/test_crud.py` | The data layer: create, read, update, delete, filtering, pagination, the UNIQUE constraint, and CASCADE deletion |
| `tests/test_api.py` | The HTTP layer: 200, 201, 204, 401, 404, 409, and 422 paths, plus API-key authentication on every protected endpoint |

### Continuous integration

`.github/workflows/test.yml` runs on every push and pull request. It starts a PostgreSQL 16 service, applies migrations, **reverses them, reapplies them**, and then runs the suite.

That up-down-up cycle caught a real bug on its first run. `downgrade()` dropped both tables but left the `stage_enum` type behind, so reapplying failed with `DuplicateObject`. Alembic's autogenerate creates enums implicitly as a side effect of `create_table` but does not write the matching `DROP TYPE`. Fixed with an explicit drop in `downgrade()`.

A local database never surfaces this, because it only ever migrates forward.

## Migrations

```bash
alembic upgrade head                              # apply all migrations
alembic revision --autogenerate -m "description"  # generate after model changes
alembic downgrade -1                              # roll back one migration
alembic current                                   # show current revision
```

When running via Docker, migrations are applied automatically as part of the API container's startup command.

Autogenerated migrations are drafts — review them before applying. Alembic detects added and removed tables and columns, but cannot infer renames or data transformations, and does not handle enum teardown (see above).

## Persistence

Data survives container restarts because PostgreSQL's data directory is mounted to a named Docker volume:

```yaml
volumes:
  - pgdata:/var/lib/postgresql/data
```

To verify:

```bash
docker compose down      # destroys the containers
docker compose up -d     # creates new ones
curl -H "X-API-Key: dev-secret-key" http://localhost:8000/candidates
```

The seeded candidates are still present.

To wipe the data as well:

```bash
docker compose down -v   # -v removes the volume
```

## Containerisation

Both services are defined in `docker-compose.yml`:

- **`db`** — PostgreSQL 16, with a `pg_isready` healthcheck
- **`api`** — built from the `Dockerfile`, waits for `db` to report healthy via `depends_on: condition: service_healthy`

The API image pins Python 3.12 rather than tracking the host's version, so the build is reproducible across machines. `requirements.txt` is copied and installed before the application code, so Docker's layer cache avoids reinstalling dependencies when only source files change.

`.dockerignore` excludes `.venv/`, `.git/`, `__pycache__/`, and `.env` from the build context. Excluding `.env` matters: without it, real credentials would be baked into the image.

## Configuration

All configuration comes from environment variables. `.env` is gitignored; `.env.example` documents the required keys.

| Variable | Purpose |
|---|---|
| `POSTGRES_USER` | database user |
| `POSTGRES_PASSWORD` | database password |
| `POSTGRES_DB` | database name |
| `POSTGRES_HOST` | `db` inside Docker, `localhost` when running the API on the host |
| `POSTGRES_PORT` | `5432` |
| `API_KEY` | value expected in the `X-API-Key` header |
| `TEST_DATABASE_URL` | connection string for the test database; defaults to a local `appdb_test` |

`API_KEY` falls back to `dev-secret-key` so the project runs immediately after cloning. In a real deployment this fallback should be removed so a missing key fails at startup rather than silently accepting a publicly known value.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | no | returns candidate count |
| POST | `/candidates` | yes | 201, or 409 on duplicate email |
| GET | `/candidates` | yes | filter by `stage`, search `q`, `limit`/`offset` |
| GET | `/candidates/{id}` | yes | 404 if absent |
| PATCH | `/candidates/{id}` | yes | partial update |
| DELETE | `/candidates/{id}` | yes | 204; cascades to interviews |

## Known limitations

- Interviews have no HTTP endpoints yet; they are populated by the seed script and queried directly.
- Candidates are hard-deleted. A production hiring system would soft-delete with a `deleted_at` column to preserve interview history for audit.
- The test fixture builds the schema with `create_all` rather than by running migrations, so the tests exercise the models. CI covers migrations separately with the up-down-up cycle described above.
- Dependencies are pinned with `>=` rather than exact versions. The Dockerfile pins the interpreter, which mitigates this, but a production project would use a lockfile.