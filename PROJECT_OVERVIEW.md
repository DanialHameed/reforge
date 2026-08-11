# ReForge — Project Overview

## Problem statement

Teams create content once but must adapt it for YouTube, Instagram, Facebook, X (Twitter), and LinkedIn—each with different formats, lengths, hashtags, and media rules. Doing this manually is slow, error-prone, and hard to scale. ReForge automates multi-platform preparation and publishing while keeping humans in the loop for review, assisted channels, and OAuth-based access to real accounts.

## Solution overview

ReForge is a **monorepo** with a **FastAPI** backend and a **Next.js 14 (App Router)** frontend. Users upload media (or use seeded demo content), trigger AI-assisted analysis and variant generation, edit per-platform captions, connect social accounts via OAuth, then publish selectively or to all connected platforms. Background work runs on **Celery** with **Redis**; media is stored on **Cloudinary** (with a local fallback for dev); data lives in **PostgreSQL** (SQLite for local dev).

## Core features

| Area | What it does |
|------|----------------|
| **Auth** | Register / login, JWT access tokens, refresh rotation, password hashing (bcrypt). |
| **Content** | Upload (validated MIME + magic bytes), list, detail, patch (e.g. schedule), process pipeline. |
| **AI** | Gemini primary, OpenRouter fallback, static safe fallback; explicit startup validation and provider attribution in logs. |
| **Variants** | One row per platform per content item: caption, hashtags, metadata, status, `published_at` for analytics. |
| **Connections** | OAuth for YouTube, Meta (Facebook/Instagram), X, LinkedIn; encrypted token storage (Fernet). |
| **Publishing** | Per-variant Celery task with retries, media preflight, native publishers per platform, master publish-all / publish-selected. |
| **Queue** | Kanban-style drafts vs scheduled, drag-and-drop reschedule, bulk actions (with modals, no `window.prompt`). |
| **Analytics** | Summary API and charts over published variants (`published_at` set). |
| **Evaluation mode** | Optional `EVALUATION_MODE`: blocks destructive deletes/disconnects; dry-run publish response when nothing would dispatch (no external API calls). |

## AI workflow

1. User uploads a file → stored (Cloudinary or local URL in DB).
2. `POST /api/v1/content/{id}/process` enqueues Celery work.
3. Pipeline resolves keys via `app/core/ai_providers.py`, calls Gemini (and OpenRouter on failure), writes platform variants and activity logs.
4. Generic fallback phrases are detected; publishing can be blocked until the user explicitly acknowledges placeholder captions.

## Publishing workflow

1. User connects platforms under **Connections** (tokens encrypted at rest).
2. From **Content detail**, user can publish one platform, **Publish selected**, or **Publish all** (master publisher).
3. Master publisher intersects requested platforms with **connected** accounts that have usable tokens, then dispatches one Celery task per variant.
4. `publish_content_task` sets status to `publishing`, validates media, routes to the correct publisher, sets `published` + `published_at` on success; `RetryablePublishError` surfaces Celery retries without marking the variant permanently failed.

## Security improvements (high level)

- Strong JWT + Fernet validation at startup in non-local environments.
- OAuth refresh/access tokens encrypted; no encryption with placeholder-derived keys in production paths.
- Upload allow-list, server-controlled filenames, magic-byte verification, safe static file path resolution.
- API middleware: CSP (strict for JSON routes; relaxed only for `/docs`), frame denial, nosniff, referrer and permissions policies, HSTS when HTTPS + non-local.
- Nginx (production compose): rate limits on auth routes, security headers, body size aligned with API.
- DB CHECK constraints on content and platform variant statuses to prevent silent corruption.

## Production architecture

- **API**: Uvicorn + FastAPI, behind Nginx in `docker-compose.prod.yml`.
- **Workers**: Celery worker + beat; Redis as broker and result backend; eager mode forced off outside `ENV=local`.
- **DB**: PostgreSQL + Alembic migrations only in production (no `create_all` outside local).
- **Deploy**: CI/CD runs `alembic upgrade head` before traffic swap.

## Testing summary

- **Backend**: 360+ pytest tests covering JWT/Fernet, AI provider resolution, migrations/schema drift, upload validation, security headers, publish retry semantics, runtime/Celery guards, deployment artifacts, demo seed idempotency, and evaluation mode behavior.
- **Frontend**: `npx tsc --noEmit`, `npm run lint` (Next.js ESLint).

## Future scope (not in current release candidate)

- Per-platform publish idempotency keys to handle “platform succeeded, DB commit failed” edge cases.
- Splitting long-lived DB sessions inside the publish task without a large publisher refactor.
- Deeper observability (OpenTelemetry, Sentry) and automated E2E browser tests.
- Additional platforms or richer scheduling rules as product priorities dictate.

For demo setup, see [DEMO.md](DEMO.md). For presenter scripts, see [PRESENTATION_GUIDE.md](PRESENTATION_GUIDE.md). For operators, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
