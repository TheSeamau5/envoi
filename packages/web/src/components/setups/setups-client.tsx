/**
 * Setups Client — wraps SetupCompare with full-data fetching.
 * Uses TanStack Query to fetch all trajectory data (with commit histories),
 * then renders SetupCompare once loaded.
 */

"use client";

import type { Trajectory } from "@/lib/types";
import { SetupCompare } from "@/components/compare/setup-compare";
import { PageHeader } from "@/components/page-shell";
import { SetupsPageSkeleton } from "@/components/page-skeletons";
import { useProjectSetups } from "@/lib/project-data";

type SetupsClientProps = {
  /** Summary-level trajectories from the server (fallback while loading) */
  allTraces: Trajectory[];
  project: string;
};

function dedupeTrajectoriesById(traces: Trajectory[]): Trajectory[] {
  const deduped = new Map<string, Trajectory>();
  for (const trace of traces) {
    deduped.set(trace.id, trace);
  }
  return [...deduped.values()];
}

export function SetupsClient({ allTraces, project }: SetupsClientProps) {
  const compareQuery = useProjectSetups(
    project,
    dedupeTrajectoriesById(allTraces),
  );

  if (compareQuery.isError) {
    return (
      <div className="flex flex-1 flex-col overflow-hidden">
        <PageHeader title="Setup Compare" />
        <div className="flex flex-1 items-center justify-center">
          <span className="text-[12px] text-red-500">
            Failed to load trajectory data
          </span>
        </div>
      </div>
    );
  }

  const traces = compareQuery.data;
  const showSkeleton =
    (traces === undefined || traces.length === 0) && compareQuery.isPending;

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <PageHeader title="Setup Compare" />
      {showSkeleton ? (
        <SetupsPageSkeleton />
      ) : (
        <SetupCompare
          allTraces={dedupeTrajectoriesById(
            traces && traces.length > 0 ? traces : allTraces,
          )}
        />
      )}
    </div>
  );
}
