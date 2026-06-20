# Multi-stage build: frontend on Node, then everything on Python+Node base.
# Build context is the poc/ folder — the monorepo root render.yaml uses
# runtime: docker with dockerContext ./poc. Same image deploys to AWS ECS.

FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# Backend deps
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Backend source (includes backend/data/aicure.db shipped via Git LFS)
COPY backend ./backend

# Built frontend
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV PORT=8080
EXPOSE 8080
WORKDIR /app/backend

# entrypoint.sh seeds the EFS-mounted DB from the baked-in copy on first boot
# (empty volume), then launches uvicorn. The seed MUST run before Python imports
# `db`, which opens the DB at import time (db._init_db()).
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Drop root (defense in depth). The app writes the live SQLite DB + uploads under
# the EFS mount at /data (AICURE_DB_PATH), so that dir must be writable by this
# user — create it owned by `app`. A fresh Docker named volume inherits this
# mountpoint's ownership, so a local `-v vol:/data` run can seed too. The baked
# seed at /app/backend/data stays read-only and is never written.
# IMPORTANT: on ECS the EFS access point MUST set POSIX uid/gid = 10001 to match,
# or the first-boot seed cp to /data fails with permission denied (deferred P2-6).
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -M -s /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data
USER app

ENTRYPOINT ["/entrypoint.sh"]
