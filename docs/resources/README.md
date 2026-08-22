# Course Resources

Reference material from UCT for this project. Not part of the codebase, kept here
so the whole team has them versioned alongside the spec.

- **project-brief.pdf** — the official ECO5040W class project brief (scope, functional
  requirements, deliverables, assessment weighting, team allocations, dates).
- **project-resources.pdf** — supplementary resource pack: XRPL trust line/IOU
  fundamentals, Xaman wallet walkthrough, `xrpl-py` programmatic flow, the RLUSD
  liquidity contingency (fallback IOU if Ripple doesn't fund UCT's wallet in time),
  and recommended tooling for backend/DB/queue/deployment/security.

## Things from project-resources.pdf that affect the code directly

- **RLUSD issuer must stay a config value, not a hardcoded constant.** The official
  RLUSD Testnet faucet caps liquidity at ~$10/24h per wallet, so UCT may need to fall
  back to their own IOU token if Ripple doesn't fund the class wallet in time.
  `RLUSD_ISSUER_ADDRESS` in `.env.example` is already wired for this, swapping issuers
  should be a one-line change, never a rewrite. Document this contingency in the
  spec's Assumptions and Limitations section either way.
- **Trust lines reserve 2 XRP** on top of the base account reserve, factor this into
  faucet funding amounts when testing.
- **If liquidity runs out**, contact Marc (LVNMAR013@myuct.ac.za) for RLUSD topped up
  from UCT's wallet.
