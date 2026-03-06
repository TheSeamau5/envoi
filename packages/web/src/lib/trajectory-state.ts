import type { Trajectory } from "@/lib/types";

export const SUBSTANTIAL_WORK_PARTS = 3;

/** Whether a trajectory has enough recorded work to count as an active attempt. */
export function hasSubstantialTrajectoryWork(trace: Trajectory): boolean {
  return (trace.totalParts ?? 0) >= SUBSTANTIAL_WORK_PARTS;
}

/** Shared active/failed bucketing for list and setup surfaces. */
export function isTrajectoryActive(
  trace: Trajectory,
  options?: { live?: boolean },
): boolean {
  if (options?.live === true) {
    return true;
  }
  if (trace.finalPassed > 0) {
    return true;
  }
  return hasSubstantialTrajectoryWork(trace);
}
