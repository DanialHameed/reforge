## ReForge — AI-powered content automation SaaS

Production-grade monorepo for:
- **frontend/**: Next.js 14 (App Router)
- **backend/**: FastAPI (Python 3.11)
- **nginx/**: Reverse proxy for prod

### Quickstart (local dev)

1) Copy env template:

```bash
cp .env.example .env
```

2) Start backend + frontend:

#### Option A: Docker (recommended when available)

```bash
cd backend
docker compose up --build
```

Then open:
- **Frontend**: `http://localhost:3000`
- **Backend health**: `http://localhost:8000/api/v1/health`
- **Backend docs**: `http://localhost:8000/docs`

#### Option B: Windows / no Docker

Backend (PowerShell):

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\pip.exe install -r backend\requirements.txt
backend\.venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend (new terminal):

```powershell
cd frontend
npm install
npm run dev -- --port 3000
```

### Useful commands

```bash
make dev
make logs
make down
```

### Repo layout
- **frontend/src/app/**: routes + layouts
- **frontend/src/components/**: UI components
- **frontend/src/lib/**: API client + utilities
- **frontend/src/hooks/**: reusable hooks
- **frontend/src/types/**: shared TS types
- **frontend/src/stores/**: client state stores
- **backend/app/api/v1/**: API routes
- **backend/app/services/**: business logic
- **backend/app/services/publishers/**: external publishing integrations
- **backend/app/workers/**: async/background workers (no Redis/BullMQ)

### Documentation
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — problem, solution, architecture, security, testing
- [PRESENTATION_GUIDE.md](PRESENTATION_GUIDE.md) — demo script and evaluator Q&A
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — OAuth, env, ports, AI fallbacks, recovery commands

### Notes
- **No local AI models**: integrate via external AI APIs only.
- **No Redis / BullMQ**: background work uses DB-backed jobs and/or simple asyncio workers.
