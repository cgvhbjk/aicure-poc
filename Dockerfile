# Multi-stage build: frontend on Node, then everything on Python+Node base.
# Used when Render's native Python runtime can't run npm. Switch render.yaml
# to `runtime: docker` to use this.

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

# Backend source
COPY backend ./backend

# Built frontend
COPY --from=frontend /app/frontend/dist ./frontend/dist

# Seed the DB during build so the running container starts ready-to-serve.
# Render's free tier has no persistent disk; rebuilt on every deploy.
RUN cd backend && python3 ingest.py

ENV PORT=10000
EXPOSE 10000
WORKDIR /app/backend
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT}
