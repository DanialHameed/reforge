# ReForge — Demo & Evaluation Guide

A focused reference for evaluators and operators running the demo.
Everything below reflects the current code in this commit; nothing is aspirational.

---

## 1. Demo quick-start (90 seconds to a working stage)

```bash
# Backend
cd backend
python -m venv .venv && .venv\Scripts\activate            # Windows
# or:  source .venv/bin/activate                          # macOS / Linux
pip install -r requirements.txt
alembic upgrade head                                      # creates schema
python scripts/seed_demo_data.py                          # idempotent demo data
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev -- --port 3000
```

Open `http://localhost:3000` → land on `/login`. Sign in with the seeded user:

| Email | Password |
|---|---|
| `demo@reforge.dev` | `ReForge!Demo123` |

You will land on `/dashboard` with 6 content items, 20 platform variants, and a populated activity feed. The Queue page shows scheduled items; the Analytics page shows non-empty charts because the seed includes published variants spanning the last 30 days.

`scripts/seed_demo_data.py` is idempotent — re-run it any time to refresh the demo state without duplicating rows.

### Evaluation mode (optional, safe demos)

For review sessions where you want to avoid accidental deletes or disconnects:

1. In the repo root `.env` (or backend env): `EVALUATION_MODE=true`
2. In `frontend/.env.local`: `NEXT_PUBLIC_EVALUATION_MODE=true` (must match so the UI disables destructive controls and shows the banner)

Effects: content delete and platform disconnect return **403** from the API; publish with **no** connected targets returns a **dry-run** JSON with a clear message instead of attempting external dispatch. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and [PRESENTATION_GUIDE.md](PRESENTATION_GUIDE.md).

---

## 2. Architecture summary

```
┌──────────────────┐  HTTPS/HTTP  ┌──────────────────┐  ASGI  ┌──────────────────┐
│ Browser          │ ───────────► │ Nginx (prod)     │ ─────► │ FastAPI (uvicorn)│
│ Next.js 14 SPA   │ ◄─────────── │ rate-limit + CSP │ ◄───── │ + middleware     │
└──────────────────┘              └──────────────────┘        └────────┬─────────┘
                                                                       │
                                                            ┌──────────┼──────────┐
                                                            │          │          │
                                                            ▼          ▼          ▼
                                                       Postgres    Celery      Cloudinary
                                                       (asyncpg)   workers     (media CDN)
                                                                   ▲   ▲
                                                                   │   │ broker / backend
                                                                   └─Redis
```

- **Same-origin proxy in dev**: Next.js rewrites `/ingest-reforge/*` → `BACKEND_INTERNAL_URL` (default `http://127.0.0.1:8000`). No CORS surprises in the demo.
- **Three FastAPI middlewares** (in order): CORS → access log → security headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, conditional HSTS).
- **Four startup validators** in `app/main.py::lifespan`:
  1. `validate_security_config_at_startup` — refuses to boot non-local env without a real JWT signing secret + valid Fernet key.
  2. `validate_ai_provider_config_at_startup` — requires at least one of Gemini / OpenRouter; logs which provider serves traffic.
  3. `validate_production_runtime_at_startup` — forbids eager Celery, in-memory broker / backend, and SQLite in any non-local environment.
  4. Schema bootstrap — `Base.metadata.create_all` runs **only** when `ENV=local`. Production schema is owned exclusively by Alembic.

---

## 3. Tech stack

**Backend** (Python 3.11)
- FastAPI · SQLAlchemy 2.0 (async) · Alembic · Pydantic v2 · `pydantic-settings`
- Celery 5 · Redis · `tenacity` · `httpx`
- `python-jose` (JWT) · `passlib[bcrypt]` · `cryptography` (Fernet token-at-rest)
- Cloudinary SDK · ffmpeg · Pillow

**AI**
- Google Gemini (primary)
- OpenRouter (fallback)
- Static safety fallback (final)

**Frontend** (Node 20)
- Next.js 14 (App Router) · React 18 · TypeScript
- Zustand (auth store) · `@tanstack/react-query` (data fetching) · Axios
- Tailwind CSS · Framer Motion · Recharts · `@dnd-kit/core`

**Infrastructure**
- Docker · `docker-compose.prod.yml` (Nginx + API + Celery worker + Redis + Postgres)
- GitHub Actions deploy pipeline (`appleboy/ssh-action`) — runs `alembic upgrade head` inside the API container before service swap.

---

## 4. AI pipeline summary

`backend/app/services/`:

```
GeminiService ──► (success) ──► AIProviderResult{ provider="gemini" }
      │
      └─ (transport / quota / 429) ──► OpenRouterService ──► result{ provider="openrouter" }
                                              │
                                              └─ (failure) ──► static fallback ──► result{ provider="static_fallback" }
```

- **Single source of truth for keys**: `app/core/ai_providers.py::resolve_gemini_key()` and `resolve_openrouter_key()`. Strips quotes, rejects `change-me`-style placeholders, and reports the `(env, source_name)` pair so logs explicitly show *which* env var was honored.
- **Logged at every call**: a single line `ai.provider.used provider=… model=… key_source=…`. Operators can grep one log to see whether Gemini, OpenRouter, or the static fallback served a particular request.
- **Fallback content gate**: `master_publisher` refuses to publish AI fallback captions unless the operator explicitly acknowledges `acknowledge_placeholder_captions=true`. Prevents accidentally posting "amazing content worth sharing" to a real account.

---

## 5. Publishing workflow summary

1. **Upload** (`POST /api/v1/content/upload`) — three-layer validation (MIME allow-list → server-controlled extension → magic-byte sniff). Stored on Cloudinary, with a local-disk fallback for dev.
2. **Process** (`POST /api/v1/content/{id}/process`) — Celery task analyzes the media (Gemini Vision), then generates one `PlatformVariant` per platform with platform-tuned caption + hashtags.
3. **Publish**:
   - `POST /api/v1/platforms/publish-all/{content_id}` — broadcasts to every connected platform.
   - `POST /api/v1/platforms/publish-selected/{content_id}` — subset.
   - `POST /api/v1/platforms/{platform}/publish/{content_id}` — single platform.
4. **Per-platform Celery task** (`backend/app/workers/publish_task.py`):
   - Marks variant `publishing` (early so the UI shows progress).
   - Runs media preflight + auto-fix for Cloudinary aspect/crop.
   - Dispatches to the per-platform publisher.
   - On `RetryablePublishError` (rate-limit) → reverts status, increments `retry_count`, and re-raises so Celery's `self.retry(countdown=...)` fires. **Critical bug fix in P-6**: previously this exception was swallowed by a broad `except Exception`, silently converting every 429 to a permanent failure.
   - Every log line carries `{platform_variant_id, platform, task_id}` for triage.
5. **Frontend polling** — adaptive interval (3s active / 12s when tab hidden), automatically stops when no work is processing.

---

## 6. Security improvements summary

| Layer | What ships now |
|---|---|
| **Auth** | JWT secret hardened: rejects empty / `change-me` / known weak values, enforces ≥32 chars in non-local env. Refresh tokens rotate. |
| **OAuth tokens at rest** | Fernet-encrypted via `app/services/token_crypto.py`. Refuses to encrypt with placeholder-derived keys. Backward-compatible decrypt for legacy tokens during rotation. |
| **Upload validation** | Three layers in `app/services/upload_validation.py`: MIME allow-list (8 formats), server-controlled extension (no path traversal via filename), magic-byte sniff (rejects HTML/PE/PHP masquerading as images). |
| **Static file serving** | `/api/v1/content/uploads/{filename}` `realpath`-resolves and asserts the candidate stays under the uploads root; 404 for any escape. |
| **App-level CSP** | `app/main.py::_security_headers` middleware: lockdown CSP (`default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`) on every API/JSON response, permissive CSP only on `/docs` and `/openapi.json` (Swagger needs CDN scripts). |
| **Always-on headers** | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` locks geolocation/camera/microphone/payment/USB. |
| **HSTS** | Emitted only over HTTPS *and* `ENV != "local"` — never pin localhost. |
| **Edge rate limiting** | `nginx/nginx.prod.conf` — `limit_req_zone` on `/api/v1/auth/*` to throttle credential stuffing. |
| **Schema integrity** | `platform_variants.status` and `content_items.status` both have CHECK constraints in Postgres (migrations 005 + 007), backed by a single source-of-truth enum in `app/models/`. |

---

## 7. Production readiness summary

- **Schema**: Alembic-only in production. Migrations 001–007 are fully exercised by 32 schema-drift / constraint regression tests. `create_all` is gated to `ENV=local` so the bug class that masks ORM/migration drift cannot recur.
- **Process model**: Eager Celery is **forced off** in `app/workers/celery_app.py` for any non-local environment, regardless of env-flag values, with an ERROR log emitted if eager mode was requested. Defense-in-depth.
- **Healthchecks**:
  - `GET /health` and `GET /api/v1/health` — liveness.
  - `GET /health/ready` — Postgres ping + Redis ping; returns **HTTP 503** when either is unreachable so load balancers stop sending traffic.
- **Tests**: 361 backend tests across schema migrations, JWT/Fernet hardening, upload validation, security headers, AI provider resolution, publisher retry semantics, runtime validation, deployment artifacts, and the demo seeder.
- **Frontend**: `npx tsc --noEmit` and `npm run lint` both clean. Polling intervals are visibility-aware. Per-mutation toasts. Modal dialogs replaced all `window.prompt`/`window.confirm` for consistent UX and proper focus management.
- **Observability**: Structured `extra=` log fields throughout — `platform_variant_id`, `platform`, `task_id`, `retries`, `key_source`, `provider`. Greppable.

### What is NOT in this build (intentional, documented)

- No platform OAuth tokens are seeded. Connect your own platform accounts at demo time via `/connections`.
- The publish-task DB session is held across the entire upload (up to 25 minutes for video). Pool sizing accounts for it; splitting it would touch every native publisher and is out of scope for hardening.
- "Platform-side success but final commit failed" race in `_publish_variant` is documented but not solved — would require per-platform idempotency tokens.
- Cloudinary missing in production is a **notice**, not a hard failure (local-fallback path still works); operators see a clear `runtime.notice` log line at boot.

---

## 8. Demo evaluator journey (in order)

1. Visit `/` → auto-redirects to `/login` (or `/dashboard` if a session exists).
2. Sign in as `demo@reforge.dev` / `ReForge!Demo123`.
3. **Dashboard** — three stat cards (processing / drafts / scheduled) + the recent-content table.
4. **Content** — list of 6 items mixed across `published` / `scheduled` / `draft`.
5. **Content detail** (click any row) — see the AI-generated platform variants with editable captions, per-platform publish buttons, and the **Publish All** action.
6. **Connections** — connect any subset of YouTube / Instagram / Facebook / Twitter-X / LinkedIn via real OAuth.
7. **Queue** — drag drafts into Scheduled, reschedule via the modal date-picker, bulk-delete via the confirm modal.
8. **Analytics** — populated charts because the seed produced published variants over the last 22 days.

---

## 9. One-page test invocation cheatsheet

```bash
# Backend full suite (361 tests)
cd backend && python -m pytest -q

# Frontend health
cd frontend && npx tsc --noEmit && npm run lint

# Lifespan smoke (boots app.main and runs every startup validator)
cd backend && python -c "import asyncio; from app.main import app; asyncio.run((async def f(): \
    [None async for _ in app.router.lifespan_context(app).__aiter__()])())"

# Production deployment artifacts (16 tests, no Docker required)
cd backend && python -m pytest tests/test_deployment_artifacts.py -q
```
