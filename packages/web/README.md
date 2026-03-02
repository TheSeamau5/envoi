# envoi dashboard

Dashboard for analyzing AI coding agent traces. Compare model performance, drill into individual trajectories, and identify training signal for post-training pipelines.

## Quick Start

```bash
cd packages/web
pnpm install
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000).

## Pages

- **/compare** — Compare 2-4 traces side-by-side: progress curves, milestone divergence, per-suite breakdowns. Or group by model/language for aggregate comparisons.
- **/trajectory/[id]** — Deep-dive into a single trace: timeline with playback, agent steps, and IDE-style code viewer.

## Stack

Next.js 15 · TypeScript · Tailwind CSS v4 · shadcn/ui · lucide-react · Custom SVG charts

## Data

Currently uses deterministic mock data (30 trajectories across 6 models). The mock generator produces realistic C compiler agent traces with proper suite progression ordering.
