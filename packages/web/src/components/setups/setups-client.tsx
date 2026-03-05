/**
 * Setups Client — wraps SetupCompare with full-data fetching.
 * Uses TanStack Query to fetch all trajectory data (with commit histories),
 * then renders SetupCompare once loaded.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import type { Trajectory } from "@/lib/types";
import { SetupCompare } from "@/components/compare/setup-compare";
import { queryKeys } from "@/lib/query-keys";

type SetupsClientProps = {
  /** Summary-level trajectories from the server (fallback while loading) */
  allTraces: Trajectory[];
  project: string;
};

export function SetupsClient({ allTraces, project }: SetupsClientProps) {
  const compareQuery = useQuery({
    queryKey: queryKeys.compare.all(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/compare?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch trajectory data");
      }
      const data: Trajectory[] = await response.json();
      return data.filter((trace) => trace.finalPassed > 0);
    },
  });

  if (compareQuery.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <span className="text-[12px] text-envoi-text-muted">
          Loading trajectory data...
        </span>
      </div>
    );
  }

  if (compareQuery.isError) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <span className="text-[12px] text-red-500">
          Failed to load trajectory data
        </span>
      </div>
    );
  }

  const traces = compareQuery.data;

  return (
    <SetupCompare
      allTraces={traces && traces.length > 0 ? traces : allTraces}
    />
  );
}
