"use client";

import type { Trajectory } from "@/lib/types";
import { useProjectTrajectoryDetail } from "@/lib/project-data";

export function isPossiblyLive(trajectory: Trajectory): boolean {
  return !trajectory.sessionEndReason;
}

export function useLiveTrajectory(
  initial: Trajectory,
  project: string,
): {
  trajectory: Trajectory;
  isLive: boolean;
  lastRefreshed: Date;
} {
  const detail = useProjectTrajectoryDetail(project, initial.id, initial);
  return {
    trajectory: detail.trajectory ?? initial,
    isLive: detail.isLive,
    lastRefreshed: detail.lastRefreshed,
  };
}
