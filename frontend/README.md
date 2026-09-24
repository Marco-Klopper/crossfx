# CrossFX Frontend

**React 18 + Vite 5.** A single-page app covering the whole journey the brief
describes — register, KYC, recipients, quote, cash-in, wallet, cash-out — plus the
administrator interface.

No router, no state library, no UI framework. Six screens behind one login is not
enough to earn any of them, and every dependency is one more thing to explain.

## Running it

The frontend talks to the backend over HTTP, so **start the API first**:

```bash
# terminal 1 — the API
cd backend
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
uvicorn app.main:app --reload   # http://127.0.0.1:8000

# terminal 2 — this app
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

Port **5173 is not optional**: `vite.config.js` sets `strictPort`, so Vite exits
rather than sliding to 5174, and the backend's `CORS_ORIGINS` only allows 5173 and
3000. If the port is busy, free it rather than letting Vite pick another.

To point the app at an API somewhere other than `http://127.0.0.1:8000`, set
`VITE_API_BASE` — in `frontend/.env.local`, or inline:

```bash
VITE_API_BASE=http://192.168.1.20:8000 npm run dev
```

### Seeing the Admin tab

Admin routes need an account with `is_admin`, which no API route can grant. Create
one from the backend:

```bash
cd backend
python -m scripts.create_admin --email admin@example.com --password adminpass123
```

Log in as that account and an **Admin** tab appears. `GET /auth/me` reports
`is_admin`, which is what the tab is driven from.

## Scripts

| Command | Does |
|---|---|
| `npm run dev` | Vite dev server on 5173, with HMR |
| `npm run build` | Production bundle into `dist/` |
| `npm run preview` | Serve the built bundle, to check it before a demo |
| `npm run lint` | ESLint, including `react-hooks` |
| `npm test` | Vitest, once |
| `npm run test:watch` | Vitest, watching |

CI runs lint, test and build on every pull request (`.github/workflows/ci.yml`).

## Layout

```
src/
├── main.jsx              # mounts App
├── App.jsx               # session state, the tab bar, which screen is mounted
├── api.js                # the ONLY place this app calls the backend
├── ui.jsx                # Badge, Alert, Line, TxHash + the money formatters
├── styles.css            # one stylesheet, no CSS framework
└── screens/
    ├── Auth.jsx          # register + login
    ├── Kyc.jsx           # the eight-field mock KYC form, status, limits
    ├── Beneficiaries.jsx # add, list, remove recipients
    ├── Send.jsx          # quote → confirm cash-in → watch it settle
    ├── Wallet.jsx        # balance, transaction history, cash-out request
    └── Admin.jsx         # KYC review, cash-in confirmation, payout approval
```

Six screens, not eight: "simulated cash-in confirmation" and "cash-out request" are
cards inside `Send.jsx` and `Wallet.jsx` rather than separate destinations, because
each only makes sense immediately after what precedes it.

### `api.js`

Every screen imports from here rather than calling `fetch` itself, so the base URL,
the JWT and the error shape are defined once. Three things it handles that are easy
to miss:

- **Trailing slashes** on `/beneficiaries/` and `/remittances/` are deliberate. Without
  them FastAPI answers 307, and a cross-origin redirect drops the `Authorization`
  header — which surfaces as a baffling 401.
- **A 401 ends the session.** The token is cleared *and* `App` is told, so an expired
  token returns you to the login screen instead of a shell where every click fails.
  Tokens last 60 minutes, so this matters in any session longer than that.
- **Amounts are sent as strings**, straight from the input, so Pydantic parses them to
  `Decimal` with no float round trip.

### Money formatting

`ui.jsx` formats decimal strings with `BigInt` arithmetic and never converts them to
a JS number. Everything uses one convention — thin space for thousands, dot for the
decimal point — so a quote breakdown does not show `R1 000,00` on one row and
`18.500000` on the next.

## Known limitations

- **The token is in `localStorage`**, which is readable by any script on the origin.
  A prototype trade-off, recorded in the tech spec's §15; an `HttpOnly` cookie plus
  CSRF protection is the production answer.
- **No router**, so a refresh returns to the Send tab and there are no deep links.
- **No component tests.** Vitest covers `api.js` and the money formatters — the two
  places a bug is silent rather than visible — not the screens themselves.
