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
