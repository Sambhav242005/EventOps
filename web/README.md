# EventOps web — Next.js + shadcn/ui room client

Wired to the FastAPI backend (`NEXT_PUBLIC_API_URL`, default `http://localhost:8000`).

```bash
cd web
npm install
cp .env.example .env.local   # point at the backend
npm run dev                  # http://localhost:3000
```

Features: passcode login, WS room, approval buttons, backup/alert panels,
event-specific guest check-in QR, CSV export. Styling follows the Stitch
"Warm Orchestration" tokens mapped onto shadcn CSS variables.
