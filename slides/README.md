# Dispatch Guardian — Presentation Deck

Scrolling presentation site for Dispatch Guardian: always-on HOS feasibility checks, costed recovery
planning, and dispatcher approvals in Slack — running locally on the Dell Pro Max with NVIDIA GB10.

Presenters: Max Martinez, Yahil Corcino, David Rapozo.

## Slides

`Home · Problem · Stakes · Guardian · GB10 · Market · Conclusion` — one scroll-snap section each,
defined in `src/pages/Index.tsx`. Nav pills come from `MAIN_NAV_ITEMS`; every stat card carries its
own citation string.

Content is sourced from the research files in `../data/`:
- `hos_incident_research.json` — real HOS violation cases and regulatory context
- `economics_business_case.json` — violation costs, industry economics, market sizing

Keep the confidence/methodology labels visible on the Market slide. The SOM figure is an estimate and
is labeled as one.

## Setup

This project uses [Bun](https://bun.sh) (npm/node are not installed on the presentation machine).

```bash
bun install
bun run dev
```

Dev server runs on `http://localhost:8080`.

## Build

```bash
bun run build
```

## Tech Stack

Vite · TypeScript · React · shadcn-ui · Tailwind CSS · Framer Motion
