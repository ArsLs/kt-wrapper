# Keitaro Wrapper

A web app for creating Keitaro campaigns and editing offers in streams.

## Run with Docker Compose

From the project root, save the provided Keitaro settings as `.env`. If you are
setting up your own credentials, copy `.env.example` to `.env` and fill in
`KT_ADMIN_API_URL` (including `/admin_api/v1`) and `KT_ADMIN_API_KEY`.

Start Docker, then start the app and PostgreSQL.

```sh
docker compose up --build --wait
```

The app runs `alembic upgrade head` after PostgreSQL is ready, then starts serving
the UI at [http://localhost:8000](http://localhost:8000). The health endpoint
at [http://localhost:8000/api/health](http://localhost:8000/api/health) returns
`{"status":"ok","database":"ok"}` when the API can connect to PostgreSQL.
The campaign list on the home page verifies the connection to Keitaro.

If startup fails, run `docker compose logs app postgres`. If a host port is in use,
set `APP_PORT` or `POSTGRES_PORT` in `.env` and use the new port when connecting
from your machine. `docker compose down` stops the services and keeps the database
volume.

## Local development

Use Python 3.12, uv, Node.js 22.12 or later, Make, and Docker Compose. Prepare
`.env` as above, then run these commands from the project root.

```sh
docker compose up --wait postgres
uv sync --locked
npm --prefix frontend ci
make migrate
```

Run `make dev-api` and `make dev-web` in separate terminals. Open
[http://localhost:5173](http://localhost:5173). Vite forwards API requests to
port `8000`.

The local API and Alembic default to PostgreSQL on `localhost:5432`. If you set
`POSTGRES_PORT` for Compose, also set `DATABASE_URL` in `.env` using that host
port, for example `postgresql+asyncpg://kt:kt@localhost:5433/kt_wrapper`.

Run `make lint` and `make test` from the project root. The backend tests use
`kt_wrapper_test`, which Compose creates when it initializes a fresh database
volume. If PostgreSQL uses a different host port, pass `TEST_DATABASE_URL` to
`make test` with that port and the `kt_wrapper_test` database name.

For browser tests, run `npx playwright install --only-shell chromium` once from
`frontend`, then run `npm run test:e2e` from the same directory. These tests mock
API responses.

## Logs

Run `docker compose logs -f app` to see the API and Uvicorn logs. Set `LOG_LEVEL` in `.env`
to control verbosity.Use the `X-Request-ID` response header to find the HTTP event and related
Create, Push, or Keitaro events.

## Migrations

To see the revision applied to the Compose database, run
`docker compose exec app alembic current`.

For a local PostgreSQL connection, run these commands from the project root.
Alembic uses `DATABASE_URL` and can run without Keitaro credentials.

| Task | Command |
| --- | --- |
| Apply pending migrations | `make migrate` (`uv run alembic upgrade head`) |
| Show the applied revision | `uv run alembic current` |
| Check the migrated schema against SQLAlchemy models | `uv run alembic check` |
| Generate a migration after changing models | `make revision m="describe the change"` |
| Roll back one revision | `uv run alembic downgrade -1` |

Review generated migrations in `migrations/versions/` before applying them.
Stop the app and back up the database before rolling back a migration with data
you need to keep. Starting the Compose app again reapplies pending migrations.
