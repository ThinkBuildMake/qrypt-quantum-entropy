# DEPLOY.md — S01 stub service on Render, and the checks that need Render

Nothing here has been run: S01 was built without a Render account (see `docs/DEVIATIONS.md`).

1. In Render choose New, Blueprint, point it at this repository: `render.yaml` creates a free web service
   (`qrypt-entropy`) and a free Postgres 18 (`qrypt-db`) and wires `DATABASE_URL`. Do not create the production database
   before 2026-10-20 (`STACK.md` 19); this one is the spike/demo database.
2. The start command runs `alembic upgrade head` (safe to run twice), then uvicorn.
3. Checks (AC3, AC4, AC5, AC6, AC7). `RENDER_URL` is the host name without scheme:
   ```
   curl -s https://$RENDER_URL/health
   curl -sI http://$RENDER_URL/health                       # expect a 301/308 to https, or a refusal
   curl -s -X POST -H "Authorization: Bearer stub-client-key" -d '{"owner":"user-42","value":"123456"}' \
        -H 'Content-Type: application/json' https://$RENDER_URL/verify
   curl -s -H "Authorization: Bearer stub-admin-key" https://$RENDER_URL/admin/usage
   curl -s https://$RENDER_URL/admin/usage                  # expect 401 unauthorized
   make migrate migrate DATABASE_URL=$RENDER_EXTERNAL_URL   # twice; then psql -c '\dt' -c 'SELECT cap_kib, used_kib FROM budget'
   make spike-roles DATABASE_URL=$RENDER_ADMIN_URL          # expect PASS create, grant, insert, denied-select
   ```
   The external connection string needs `?sslmode=require` if it does not already carry it.
