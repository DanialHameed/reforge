# ReForge — Presentation Guide

Use this script for a confident, stable demo. **Evaluation mode** (optional): set `EVALUATION_MODE=true` on the backend and `NEXT_PUBLIC_EVALUATION_MODE=true` on the frontend so deletes/disconnects are blocked and “publish with no connections” returns a clear dry-run message instead of an error-only path.

---

## Exact demo order (what to click)

1. **Open the app** — `http://localhost:3000` (or your deployed URL). You should land on **Login** (or Dashboard if already signed in).
2. **Sign in** — Use seeded demo user after running `python backend/scripts/seed_demo_data.py`:  
   **demo@reforge.dev** / **ReForge!Demo123**
3. **Dashboard** (`/dashboard`) — Point to the three stat cards and **Recent Content** table. Click **Upload** or **View all**.
4. **Content** (`/content`) — Show mixed statuses (draft / scheduled / published). Open any row (**Open**).
5. **Content detail** (`/content/{id}`) — Scroll platform cards: captions, hashtags, per-platform **Publish** (if connected), **Publish all**, **Select platforms** for selective publish. Mention AI-generated vs edited captions.
6. **Connections** (`/connections`) — Explain OAuth once; click **Connect** on one platform only if credentials are configured; otherwise explain the flow without completing OAuth.
7. **Queue** (`/queue`) — Card vs calendar toggle; drag a draft into **Scheduled**; mention bulk bar if items are selected.
8. **Analytics** (`/analytics`) — Change date range preset; show charts fed by `published_at` on variants.

---

## What to explain (technical highlights)

| Topic | One-liner |
|-------|-----------|
| **Stack** | Next.js 14 frontend, FastAPI + SQLAlchemy + Alembic backend, Celery + Redis for async publish and AI processing. |
| **AI** | Gemini first, OpenRouter on failure, static fallback last; startup refuses to serve production without a configured provider; logs show which provider ran. |
| **Security** | JWT and Fernet validated at boot; OAuth tokens encrypted; uploads validated (MIME + magic bytes); CSP and security headers on API responses; Nginx rate limits auth in production. |
| **Publishing** | One Celery task per platform variant; retries on rate limits; `published_at` drives analytics. |
| **Schema** | Production uses Alembic only; CHECK constraints on statuses prevent bad data. |
| **Evaluation mode** | Safe demos: no accidental delete/disconnect; publish with zero connections returns an explicit dry-run payload (no external posts). |

---

## Common evaluator questions + answers

**Q: Where does the AI run?**  
A: On the backend via Google Gemini and optionally OpenRouter—no local model weights in the repo.

**Q: Is my OAuth data safe?**  
A: Refresh/access material is encrypted at rest with Fernet; production startup rejects weak secrets.

**Q: What if Cloudinary is down?**  
A: Upload can fall back to local storage in dev; production should use Cloudinary with persistent volume if needed—see TROUBLESHOOTING.

**Q: Can I delete content during the demo?**  
A: Yes in normal mode. With **evaluation mode** on, deletes and disconnects return 403 with a clear message.

**Q: Why does “Publish all” sometimes do nothing?**  
A: Nothing is dispatched if no platform is connected *or* captions are generic placeholders without acknowledgement. With evaluation mode and no connections, you get a **dry-run** success-style explanation instead.

**Q: How do I reset the database for a clean demo?**  
A: Fresh DB: run migrations, then `python scripts/seed_demo_data.py` again (idempotent).

---

## Backup explanations if APIs fail

| Symptom | What to say |
|---------|-------------|
| Login fails | “Backend URL or env secrets—check `NEXT_PUBLIC_API_BASE_URL` / proxy and that the API is running on the port Next rewrites to.” |
| Empty analytics | “Analytics count **published** variants with `published_at`. Run the seed script or publish once successfully.” |
| OAuth redirect error | “Redirect URI must match the provider console exactly; see TROUBLESHOOTING.” |
| 503 on `/health/ready` | “Redis is required when configured—broker is down or URL wrong.” |
| AI always generic | “No Gemini/OpenRouter keys in this environment—startup would fail in production; local may log CRITICAL and use fallback.” |
| Publish errors | “Each platform needs a live connection and valid media; rate limits retry automatically.” |

---

## 2-minute version

1. Login → Dashboard (“pipeline at a glance”).  
2. Content → open one item → “AI produced these five variants; we can edit and publish.”  
3. One sentence on security (encrypted tokens, validated uploads) and Celery for publish.  
**Stop.**

---

## 5-minute version

2-minute script, plus:

4. **Connections** — “OAuth per platform; tokens stored encrypted.”  
5. **Queue** — “Draft vs scheduled; drag to reschedule.”  
6. **Analytics** — “Driven by real `published_at` timestamps.”  
7. Mention **evaluation mode** if you are on a shared stage: “Deletes and disconnects are off; publish without connections is a dry run.”

---

## 10-minute version

5-minute script, plus:

- Open **API Docs** link from the header (`/docs` or proxied URL)—show **POST /upload**, **POST /process**, **publish-all**.  
- Briefly open **Network** tab: JWT on API calls, same-origin `/ingest-reforge` in dev.  
- Walk through **one** successful path you have credentials for (e.g. connect YouTube OR show publish dry-run in evaluation mode).  
- Close with **testing**: “360+ backend tests, typecheck + lint on frontend, migrations tested on fresh DB.”

---

## Checklist the night before

- [ ] Backend running, migrations applied, seed script run if needed.  
- [ ] Frontend `npm run dev` (or production build served).  
- [ ] `.env` has real AI keys if you want non-fallback AI.  
- [ ] At least one OAuth app configured if you want live publish.  
- [ ] Optional: `EVALUATION_MODE` + `NEXT_PUBLIC_EVALUATION_MODE` for safe room demos.
