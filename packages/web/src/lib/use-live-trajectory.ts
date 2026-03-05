/**
 * Hook that polls for fresh trajectory data when the trajectory is live.
 *
 * Liveness is determined by querying the sandbox provider (Modal / E2B) via
 * the /api/trajectories/:id/sandbox-status endpoint.  This is the source of
 * truth — no timestamp heuristics needed.
 *
 * Uses TanStack Query with refetchInterval for polling instead of manual
 * setTimeout chains. Polling stops automatically when sessionEndReason
 * is present or the sandbox reports as stopped.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import type { Trajectory } from "@/lib/types";
import { queryKeys } from "@/lib/query-keys";

/** Poll every 30 seconds while live */
const POLL_INTERVAL_MS = 30_000;

/**
 * Quick client-side check: if sessionEndReason is present, it's done.
 * If absent, we need to ask the sandbox provider.
 * This is used for optimistic UI — the real answer comes from the API.
 */
export function isPossiblyLive(trajectory: Trajectory): boolean {
  return !trajectory.sessionEndReason;
}

/**
 * Check sandbox status via the server endpoint (queries the actual provider).
 * Returns true if the sandbox is still running, false otherwise.
 */
async function checkSandboxStatus(
  trajectoryId: string,
  project: string,
): Promise<boolean> {
  try {
    const response = await fetch(
      `/api/trajectories/${encodeURIComponent(trajectoryId)}/sandbox-status?project=${encodeURIComponent(project)}`,
    );
    if (!response.ok) {
      return false;
    }
    const data = await response.json();
    return data.running === true;
  } catch {
    return false;
  }
}

/**
 * Returns the latest trajectory data, polling every 30 seconds while live.
 * Liveness is determined by querying the sandbox provider.
 */
export function useLiveTrajectory(
  initial: Trajectory,
  project: string,
): {
  trajectory: Trajectory;
  isLive: boolean;
  lastRefreshed: Date;
} {
  const possiblyLive = isPossiblyLive(initial);

  /** Poll for fresh trajectory data while possibly live */
  const trajectoryQuery = useQuery({
    queryKey: queryKeys.trajectories.detail(project, initial.id),
    queryFn: async () => {
      const response = await fetch(
        `/api/trajectories/${encodeURIComponent(initial.id)}?project=${encodeURIComponent(project)}&bust=${Date.now()}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch trajectory");
      }
      const data: Trajectory = await response.json();
      return data;
    },
    initialData: initial,
    enabled: possiblyLive,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.sessionEndReason) {
        return false;
      }
      return POLL_INTERVAL_MS;
    },
    staleTime: 0,
  });

  const trajectory = trajectoryQuery.data ?? initial;
  const isDone = !!trajectory.sessionEndReason;

  /** Check sandbox status — only when trajectory hasn't ended yet */
  const sandboxQuery = useQuery({
    queryKey: queryKeys.trajectories.sandboxStatus(project, initial.id),
    queryFn: () => checkSandboxStatus(initial.id, project),
    enabled: possiblyLive && !isDone,
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: 0,
  });

  const isLive = possiblyLive && !isDone && (sandboxQuery.data === true);
  const lastRefreshed = new Date(trajectoryQuery.dataUpdatedAt);

  return { trajectory, isLive, lastRefreshed };
}
