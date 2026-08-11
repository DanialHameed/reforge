# Deploying the frontend to Vercel

The frontend (`frontend/`) is a standard Next.js 14 App Router project and deploys to
Vercel as-is. The backend (FastAPI + Celery + Postgres/Redis) does **not** run on
Vercel — Vercel only hosts the Next.js app. Run the backend separately (see
`docker-compose.prod.yml` / `DEMO.md`) and point the frontend at it.

## 1. Import the repo

In the Vercel dashboard: **Add New → Project → Import** `DanialHameed/reforge`.

- **Root Directory**: set to `frontend` (this is a monorepo — Vercel must build from
  the `frontend/` subfolder, not the repo root).
- Framework preset: Next.js (auto-detected).
- Build/install commands: leave the defaults (`npm run build`, `npm install`).

## 2. Point it at your backend

By default the app calls its API through a same-origin Next.js rewrite
(`/ingest-reforge/*` → `BACKEND_INTERNAL_URL`), which only works when frontend and
backend run on the same host (local dev). On Vercel, the frontend runs on Vercel's
edge, so it must call your backend's **public** URL directly instead.

Set this environment variable in the Vercel project settings:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Public URL of your running backend, e.g. `https://your-tunnel-or-domain.example.com` (no trailing slash) |

Your backend must currently be reachable at that URL. Two common ways to get there
while running the backend locally via Docker (`docker-compose.prod.yml`):

- **Tunnel** (fastest for testing): run `ngrok http 80` (or `cloudflared tunnel`)
  against the nginx container's port and use the tunnel URL.
- **Real host**: deploy `docker-compose.prod.yml` to a VPS/cloud box with a public
  IP/domain and use that.

If the backend isn't reachable at `NEXT_PUBLIC_API_BASE_URL` when someone loads the
Vercel deployment, API calls (login, content, publishing) will fail — the dashboard
UI itself will still render.

## 3. CORS

The backend only accepts browser requests from origins listed in `ALLOWED_ORIGINS`.
Add your Vercel URL(s) to the backend's `.env`:

```env
ALLOWED_ORIGINS=http://localhost:3000,https://your-project.vercel.app
```

Restart the backend after changing this.

## 4. PWA / mobile install

The app ships a `manifest.json` and icon set (`frontend/public/`) so once deployed,
visiting the Vercel URL on a phone offers "Add to Home Screen" (Android/Chrome) or
"Add to Home Screen" from the Safari share sheet (iOS) for an app-like icon and
standalone window — no native app build required.
