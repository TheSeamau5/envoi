/**
 * Setups Client — wraps SetupCompare with full-data fetching.
 * Uses TanStack Query to fetch all trajectory data (with commit histories),
 * then renders SetupCompare once loaded.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import type { Trajectory } from "@/lib/types";
import { SetupCompare } from "@/components/compare/setup-compare";
import { PageHeader } from "@/components/page-shell";
import { SetupsPageSkeleton } from "@/components/page-skeletons";
import { queryKeys } from "@/lib/query-keys";
import { isTrajectoryActive } from "@/lib/trajectory-state";
import { useProjectRevision } from "@/lib/use-project-revision";

type SetupsClientProps = {
  /** Summary-level trajectories from the server (fallback while loading) */
  allTraces: Trajectory[];
  project: string;
};

export function SetupsClient({ allTraces, project }: SetupsClientProps) {
  useProjectRevision(project, {
    invalidatePrefixes: [queryKeys.compare.full(project)],
  });

  const compareQuery = useQuery({
    queryKey: queryKeys.compare.full(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/compare?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch trajectory data");
      }
      const data: Trajectory[] = await response.json();
      return data.filter((trace) => isTrajectoryActive(trace));
    },
    initialData: allTraces.length > 0 ? allTraces : undefined,
  });

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
          allTraces={traces && traces.length > 0 ? traces : allTraces}
        />
      )}
    </div>
  );
}
