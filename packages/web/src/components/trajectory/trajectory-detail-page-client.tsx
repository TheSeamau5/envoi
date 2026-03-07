"use client";

import type { Trajectory } from "@/lib/types";
import { useProjectTrajectoryDetail } from "@/lib/project-data";
import { PageHeader } from "@/components/page-shell";
import { TrajectoryDetailSkeleton } from "./trajectory-detail-skeleton";
import { TrajectoryDetail } from "./trajectory-detail";

type TrajectoryDetailPageClientProps = {
  project: string;
  trajectoryId: string;
  initialTrajectory?: Trajectory;
  initialRightPanelOpen: boolean;
  initialDividerPct: number;
  initialGroupByTurn: boolean;
};

/** Cache-first trajectory detail page wrapper for instant revisits. */
export function TrajectoryDetailPageClient({
  project,
  trajectoryId,
  initialTrajectory,
  initialRightPanelOpen,
  initialDividerPct,
  initialGroupByTurn,
}: TrajectoryDetailPageClientProps) {
  const trajectoryQuery = useProjectTrajectoryDetail(
    project,
    trajectoryId,
    initialTrajectory,
  );

  if (trajectoryQuery.trajectory === undefined && trajectoryQuery.isPending) {
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

  if (!trajectoryQuery.trajectory) {
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
        title={trajectoryQuery.trajectory.id}
        right={<span>{trajectoryQuery.trajectory.model}</span>}
      />
      <TrajectoryDetail
        trajectory={trajectoryQuery.trajectory}
        isLive={trajectoryQuery.isLive}
        project={project}
        initialRightPanelOpen={initialRightPanelOpen}
        initialDividerPct={initialDividerPct}
        initialGroupByTurn={initialGroupByTurn}
      />
    </div>
  );
}
