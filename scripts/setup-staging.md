# Setting Up a Staging Environment on Railway

## Option A: Railway Environments (Recommended)

Railway has built-in environment support. One project, two environments sharing the same code.

### Steps

1. **Open your Railway project** → Settings → Environments
2. **Click "New Environment"** → Name it `staging`
3. Railway automatically creates copies of all services with **separate** databases

### Configure staging variables

For each service in the `staging` environment, update these vars:

**Backend (MusicMind) — staging:**
```
MUSICMIND_DATABASE_URL=<staging postgres internal URL>
MUSICMIND_LOGS_DATABASE_URL=<staging logs postgres internal URL>
MUSICMIND_SANDBOX=false
MUSICMIND_FERNET_KEY=<same as prod or generate new>
MUSICMIND_JWT_SECRET_KEY=<generate new for staging>
MUSICMIND_APPLE_TEAM_ID=<same as prod>
MUSICMIND_APPLE_KEY_ID=<same as prod>
MUSICMIND_APPLE_PRIVATE_KEY_B64=<same as prod>
MUSICMIND_SPOTIFY_CLIENT_ID=<same as prod>
MUSICMIND_LASTFM_API_KEY=<same as prod>
MUSICMIND_ADMIN_SECRET=<same or new>
MUSICMIND_FRONTEND_URL=https://<staging-frontend-url>
```

**Worker (smartaste-worker) — staging:**
```
DATABASE_URL=<staging postgres internal URL>
MUSICMIND_FERNET_KEY=<same as backend staging>
MUSICMIND_LOGS_DATABASE_URL=<staging logs postgres internal URL>
MUSICMIND_APPLE_TEAM_ID=<same as prod>
MUSICMIND_APPLE_KEY_ID=<same as prod>
MUSICMIND_APPLE_PRIVATE_KEY_B64=<same as prod>
MUSICMIND_LASTFM_API_KEY=<same as prod>
WORKER_POLL_INTERVAL=60
```

**Admin (smartaste-admin) — staging:**
```
ADMIN_PASSWORD=<new password for staging>
ADMIN_SECRET=<same as backend staging MUSICMIND_ADMIN_SECRET>
BACKEND_URL=<staging backend internal URL>
```

### DB resets between tests

Connect to staging Postgres and run:
```bash
psql <staging-postgres-url> -f scripts/reset-staging-db.sql
```

Or from Railway CLI:
```bash
railway run -e staging psql $DATABASE_URL -f scripts/reset-staging-db.sql
```

### Promote staging to production

When satisfied with staging:
1. Railway → project → staging environment
2. Each service has "Promote to Production" option
3. Or: merge the branch (if using PR environments)

---

## Option B: PR Environments (Auto-created per branch)

1. Railway → Settings → Enable "PR Environments"
2. Create a branch: `git checkout -b staging && git push origin staging`
3. Railway auto-creates a full environment with separate DBs
4. Delete the branch to tear down the environment

**Advantage:** Completely isolated, auto-provisioned
**Disadvantage:** New Postgres URL each time (need to re-run migrations)

---

## Option C: Local Docker Compose (fastest iteration)

```bash
docker compose -f docker-compose.staging.yml up
```

Uses local Postgres with volume that resets on `docker compose down -v`.
No Railway costs. Fastest restart. But no Railway networking.

---

## Auto-reset on restart

To make the staging DB auto-reset on every deploy, add this to the backend Dockerfile:

```dockerfile
# Only in staging: reset DB before starting
RUN echo '#!/bin/sh\n\
if [ "$RAILWAY_ENVIRONMENT" = "staging" ]; then\n\
  echo "Staging: resetting database..."\n\
  python -c "import asyncio; from musicmind.scripts.reset_db import reset(); asyncio.run(reset())"\n\
fi\n\
alembic upgrade head\n\
exec uvicorn musicmind.app:app --host 0.0.0.0 --port ${PORT:-8000}' > /app/start.sh
CMD ["sh", "/app/start.sh"]
```

Or simpler: just use the SQL reset script in the deploy command.
