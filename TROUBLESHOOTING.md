# ReForge — Troubleshooting

Quick fixes for demo day and local development. **Architecture is frozen**—prefer env and process fixes over code changes.

---

## OAuth issues

| Symptom | Fix |
|---------|-----|
| `redirect_uri_mismatch` | Provider console **Authorized redirect URIs** must exactly match `GOOGLE_REDIRECT_URI`, `META_REDIRECT_URI`, `TWITTER_REDIRECT_URI`, `LINKEDIN_REDIRECT_URI` (and HTTPS vs HTTP). |
| Loop back to login after OAuth | JWT/cookie issue—ensure `SECRET_KEY` / `JWT_SECRET_KEY` set and frontend hits the same API the backend expects (`NEXT_PUBLIC_API_BASE_URL` or `/ingest-reforge` rewrite). |
| `invalid_client` | Client ID/secret wrong or env not loaded—check `.env` at **repo root** or `backend/.env`; restart uvicorn after edits. |
| LinkedIn / YouTube browser callback shows 500 then redirect | Check backend logs; often DB or token persist failure—ensure DB is up and migrations applied. |
| Meta “choose page” empty | Facebook app needs pages permission; user must admin a Page. |

---

## Environment variable issues

| Symptom | Fix |
|---------|-----|
| Settings ignored | Pydantic loads repo root `.env` then `backend/.env`. Restart the API after changes. |
| UTF-8 BOM in `.env` | Keys show as `\ufeffKEY`—save `.env` as UTF-8 without BOM (VS Code: “Save with Encoding”). |
| `JWT_SECRET_KEY` / `FERNET_KEY` rejected at startup (production) | Use a non-placeholder secret ≥32 chars for JWT in production; set a valid Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). |
| AI startup failure in production | Set `GEMINI_API_KEY` and/or `OPENROUTER_API_KEY` (non-placeholder). |

### Evaluation mode (demo safety)

Set **both**:

```env
# backend .env
EVALUATION_MODE=true

# frontend .env.local (rebuild dev server after change)
NEXT_PUBLIC_EVALUATION_MODE=true
```

- Deletes (`DELETE /api/v1/content/{id}`) and disconnects (`DELETE /api/v1/platforms/{platform}`) return **403** with a clear message.
- Publish with **no** matching connected platforms returns a **dry-run** JSON payload (`evaluation_mode`, `dry_run`, `message`)—no Celery dispatch, no external APIs.

---

## Port conflicts

| Service | Default | Override |
|---------|---------|----------|
| FastAPI | `8000` | `uvicorn app.main:app --port 8001` |
| Next.js | `3000` | `npm run dev -- --port 3001` |
| Redis / Postgres (Docker) | See `docker-compose.prod.yml` | Change host ports in compose file (host side only). |

If Next cannot reach the API on Windows, set `BACKEND_INTERNAL_URL=http://127.0.0.1:8000` in `frontend/.env.local` (avoids IPv6 localhost quirks).

---

## Cloudinary issues

| Symptom | Fix |
|---------|-----|
| Upload 500 “Cloudinary not configured” | Set `CLOUDINARY_URL` or `CLOUDINARY_CLOUD_NAME` + `CLOUDINARY_API_KEY` + `CLOUDINARY_API_SECRET`. |
| Upload works but image broken | Check `NEXT_PUBLIC` / API `base_url` for returned `secure_url`; CORS is usually not an issue for img src. |
| Local dev without Cloudinary | API falls back to `backend/uploads/` and serves files under `/api/v1/content/uploads/...`. |

---

## Gemini / OpenRouter fallback behavior

1. Keys resolved centrally in `app/core/ai_providers.py` (stripping, placeholder rejection).  
2. Gemini used first where applicable.  
3. OpenRouter used on configured failures.  
4. Static fallback last—**master publisher** may block publish if captions match generic fallback phrases until the user acknowledges.

**Logs**: search for `ai.provider.used` and `ai.providers.snapshot` at startup.

---

## Redis / Postgres startup issues

| Symptom | Fix |
|---------|-----|
| `/health/ready` returns **503** with `redis_down` | Redis URL wrong or Redis not running; fix `CELERY_BROKER_URL` / `REDIS_URL` or start Redis. |
| Celery tasks never run | Non-local: eager mode is forced off—ensure a real broker (not `memory://`). |
| `OperationalError` Postgres | `DATABASE_URL` must use `postgresql+asyncpg://...`; run `alembic upgrade head`. |
| SQLite locked | Use one writer process or switch to Postgres for multi-worker. |

---

## Frontend build issues

```bash
cd frontend
rm -rf node_modules .next   # Unix
# Windows: rmdir /s /q node_modules .next
npm install
npx tsc --noEmit
npm run lint
npm run build
```

| Symptom | Fix |
|---------|-----|
| API 404 from browser | Check Next rewrite: requests should go to `/ingest-reforge/...` or set `NEXT_PUBLIC_API_BASE_URL`. |
| CORS errors | Usually wrong API origin; align `ALLOWED_ORIGINS` on backend with the frontend origin. |

---

## Common demo recovery commands

```bash
# Backend: fresh schema + demo data (SQLite example)
cd backend
alembic upgrade head
python scripts/seed_demo_data.py
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Frontend
cd frontend
npm install
npm run dev -- --port 3000

# Full backend test suite
cd backend && python -m pytest -q
```

If something is badly wedged: stop all uvicorn/next processes, verify ports are free, restart Redis/Postgres if using Docker, then restart API → frontend.

---

## Where to get more detail

- [DEMO.md](DEMO.md) — Quickstart and evaluator journey.  
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — Architecture and scope.  
- [PRESENTATION_GUIDE.md](PRESENTATION_GUIDE.md) — Talk track and timings.
