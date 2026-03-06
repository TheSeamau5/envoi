"use client";

import { useQuery } from "@tanstack/react-query";
import type { Trajectory } from "@/lib/types";
import { queryKeys } from "@/lib/query-keys";
import { PageHeader } from "@/components/page-shell";
import { TrajectoryDetailSkeleton } from "./trajectory-detail-skeleton";
import { TrajectoryDetail } from "./trajectory-detail";

type TrajectoryDetailPageClientProps = {
  project: string;
  trajectoryId: string;
  initialRightPanelOpen: boolean;
  initialDividerPct: number;
  initialGroupByTurn: boolean;
};

/** Cache-first trajectory detail page wrapper for instant revisits. */
export function TrajectoryDetailPageClient({
  project,
  trajectoryId,
  initialRightPanelOpen,
  initialDividerPct,
  initialGroupByTurn,
}: TrajectoryDetailPageClientProps) {
  const trajectoryQuery = useQuery({
    queryKey: queryKeys.trajectories.detail(project, trajectoryId),
    queryFn: async () => {
      const response = await fetch(
        `/api/trajectories/${encodeURIComponent(trajectoryId)}?project=${encodeURIComponent(project)}`,
      );
      if (response.status === 404) {
        return undefined;
      }
      if (!response.ok) {
        throw new Error("Failed to fetch trajectory");
      }
      const data: Trajectory = await response.json();
      return data;
    },
  });

  if (trajectoryQuery.data === undefined && trajectoryQuery.isPending) {
    return <TrajectoryDetailSkeleton />;
  }

  if (trajectoryQuery.isError) {
    return (
      <div className="flex flex-1 flex-col overflow-hidden">
        <PageHeader title={trajectoryId} />
        <div className="flex flex-1 items-center justify-center text-[13px] text-red-500">
          Failed to load trajectory
        </div>
      </div>
    );
  }

  if (!trajectoryQuery.data) {
    return (
      <div className="flex flex-1 flex-col overflow-hidden">
        <PageHeader title={trajectoryId} />
        <div className="flex flex-1 items-center justify-center text-[13px] text-envoi-text-dim">
          Trajectory not found
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={trajectoryQuery.data.id}
        right={<span>{trajectoryQuery.data.model}</span>}
      />
      <TrajectoryDetail
        trajectory={trajectoryQuery.data}
        project={project}
        initialRightPanelOpen={initialRightPanelOpen}
        initialDividerPct={initialDividerPct}
        initialGroupByTurn={initialGroupByTurn}
      />
    </div>
  );
}
