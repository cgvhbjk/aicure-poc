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
Production is migrating off Render to AWS ECS Fargate + EFS (for a durable,
incrementally-updatable DB). See **[DEPLOY.md](DEPLOY.md)** for the runbook:
current status, ordered next steps, required env vars, and cutover steps.
