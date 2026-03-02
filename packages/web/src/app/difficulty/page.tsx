/**
 * Difficulty Heatmap page — server component.
 * Shows per-(category, model) pass rates as a color-coded matrix.
 */

import { getDifficultyData } from "@/lib/server/data";
import { DifficultyHeatmap } from "@/components/difficulty/difficulty-heatmap";

export default async function DifficultyPage() {
  const cells = await getDifficultyData();

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Difficulty Heatmap
        </span>
      </div>
      <div className="flex-1 overflow-auto px-[14px] py-[14px]">
        <p className="mb-3 max-w-[720px] text-[12px] leading-[1.5] text-envoi-text-muted">
          Each cell shows the <strong>aggregate pass rate</strong> for a test suite and model:
          total tests passed / total tests, pooled across all trajectories.
          Hover a cell for the exact percentage and trajectory count.
        </p>
        <DifficultyHeatmap cells={cells} />
      </div>
    </div>
  );
}
