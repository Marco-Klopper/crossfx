# CrossFX Frontend

Framework not yet decided by the team. Reasonable options for a project this size:

- **React (Vite)** — most flexibility, good if someone on the team already knows it
- **Next.js** — if you want SSR/simpler deployment on Vercel
- **Plain Jinja2 templates served by FastAPI** — fastest to stand up if the team wants to
  spend more time on the backend/XRPL integration and less on frontend tooling

Screens needed per the brief:
- Register / login
- Mock KYC form + status view
- Beneficiary list/create
- Remittance quote screen (amount in, fee/margin/RLUSD breakdown, confirm)
- Simulated cash-in confirmation
- Recipient wallet (balance, incoming/outgoing, tx hash, status)
- Cash-out request screen
- Admin panel (KYC approve/reject, confirm cash-in, approve cash-out)
