import { Suspense } from "react";
import { getDifficultyData } from "@/lib/server/data";
import { DifficultyHeatmap } from "@/components/difficulty/difficulty-heatmap";
import { LoadingSkeleton } from "@/components/loading-skeleton";

type ProjectDifficultyPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectDifficultyPage({
  params,
}: ProjectDifficultyPageProps) {
  const { project } = await params;

  return (
    <Suspense fallback={<LoadingSkeleton message="Loading difficulty data..." />}>
      <DifficultyContent project={project} />
    </Suspense>
  );
}

async function DifficultyContent({ project }: { project: string }) {
  const cells = await getDifficultyData(project);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Difficulty Heatmap
        </span>
      </div>
      <div className="flex-1 overflow-auto px-3.5 py-3.5">
        <p className="pb-3 max-w-180 text-[12px] leading-normal text-envoi-text-muted">
          Each cell shows the <strong>aggregate pass rate</strong> for a test
          suite and model: total tests passed / total tests, pooled across all
          trajectories. Hover a cell for the exact percentage and trajectory
          count.
        </p>
        <DifficultyHeatmap cells={cells} project={project} />
      </div>
    </div>
  );
}
