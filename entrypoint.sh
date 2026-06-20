#!/bin/sh
# Seed the persistent DB from the image's baked-in copy on first boot only, then
# start the app. On ECS the EFS volume is mounted at /data and AICURE_DB_PATH
# points there; on an empty volume we copy the seed once, and every later boot
# reuses the accumulated DB (this is what makes incremental writes survive
# redeploys). The copy MUST happen here, before uvicorn imports `db` — db._init_db()
# opens AICURE_DB_PATH at import and would otherwise create empty tables first.
set -eu

: "${AICURE_DB_PATH:=/data/aicure.db}"
export AICURE_DB_PATH

if [ ! -s "$AICURE_DB_PATH" ]; then
  echo "[entrypoint] seeding $AICURE_DB_PATH from image baked DB"
  mkdir -p "$(dirname "$AICURE_DB_PATH")"
  # Copy to a temp path on the same filesystem, then atomically rename. An
  # interrupted first-boot copy must NOT leave a truncated-but-nonempty file:
  # the `[ ! -s ]` test above would treat it as already-seeded on the next boot
  # and the app would open a corrupt DB.
  cp /app/backend/data/aicure.db "$AICURE_DB_PATH.tmp"
  mv "$AICURE_DB_PATH.tmp" "$AICURE_DB_PATH"
fi

exec uvicorn api:app --host 0.0.0.0 --port "${PORT:-8080}"
