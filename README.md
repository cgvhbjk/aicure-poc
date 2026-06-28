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
This is a **standalone** app. A single `render.yaml` Blueprint deploys it as one
Docker web service (FastAPI backend + built React frontend on one domain); the
same image also runs on AWS ECS Fargate + EFS (for a durable, incrementally
updatable DB). See **[DEPLOY.md](DEPLOY.md)** for status, required env vars, and
the ECS cutover steps.
