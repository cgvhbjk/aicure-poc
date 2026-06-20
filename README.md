# AiCure POC — Run Instructions

## Prerequisites
- Python 3.10+
- Node.js 18+

## Backend setup
```
cd backend
pip install -r requirements.txt
python ingest.py        # pulls data; takes 2-5 minutes on first run
uvicorn api:app --reload --port 8000
```

## Frontend setup (new terminal)
```
cd frontend
npm install
npm run dev             # opens at http://localhost:5173
```

## Deployment
This app is part of the **monorepo** (see the [root README](../README.md)) and
deploys alongside the CRM on one domain via the root `render.yaml` — it's
reverse-proxied at `/pipeline`. The standalone AWS ECS Fargate + EFS path (for a
durable, incrementally-updatable DB) is in **[DEPLOY.md](DEPLOY.md)**: status,
ordered next steps, required env vars, and cutover steps.
