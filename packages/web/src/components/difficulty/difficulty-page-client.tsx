"use client";

import type { DifficultyCell } from "@/lib/types";
import { useProjectDifficulty } from "@/lib/project-data";
import { PageHeader } from "@/components/page-shell";
import { DifficultyPageSkeleton } from "@/components/page-skeletons";
import { DifficultyHeatmap } from "./difficulty-heatmap";

type DifficultyPageClientProps = {
  project: string;
  initialCells?: DifficultyCell[];
};

/** Cache-first difficulty page shell with a page-specific cold-load skeleton. */
export function DifficultyPageClient({
  project,
  initialCells,
}: DifficultyPageClientProps) {
  const difficultyQuery = useProjectDifficulty(project, initialCells);

  const showSkeleton =
    difficultyQuery.data === undefined && difficultyQuery.isPending;

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <PageHeader title="Difficulty Heatmap" />
      {showSkeleton ? (
        <DifficultyPageSkeleton />
      ) : (
        <div className="flex-1 overflow-auto px-3.5 py-3.5">
          <p className="pb-3 max-w-180 text-[12px] leading-normal text-envoi-text-muted">
            Each cell shows the <strong>aggregate pass rate</strong> for a test
            suite and model: total tests passed / total tests, pooled across all
            trajectories. Hover a cell for the exact percentage and trajectory
            count.
          </p>
          <DifficultyHeatmap
            cells={difficultyQuery.data ?? []}
            project={project}
          />
        </div>
      )}
    </div>
  );
}
