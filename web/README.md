# Next.js Incident Command UI

Mobile-first PWA frontend for the AI Operations Command Center.

## Run

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:3000`.

The browser never receives your server API key. Next.js proxies requests through
`/api/backend/*` and injects the key server-side.

Set these variables in `web/.env.local` for local work:

```bash
BACKEND_BASE_URL=http://127.0.0.1:8000
BACKEND_API_KEY=dev-ingest-key
```

For Vercel/Railway frontend deploy, set the same variables in the frontend
service. `BACKEND_API_KEY` must be a project server key from the FastAPI
gateway. Do not expose Codex/OpenAI runtime, GitHub webhook, Supabase webhook,
or admin keys to the browser.

## Build

```bash
npm run typecheck
npm run build
```

## Low disk space note

This workspace lives on `C:`. If `npm install` fails with `ENOSPC`, copy the
`web/` folder to a roomier drive such as `D:`, install there, and keep
`BACKEND_BASE_URL` pointed at the FastAPI backend.
