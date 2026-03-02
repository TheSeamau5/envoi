/**
 * Hook that polls for fresh trajectory data when the trajectory is live
 * (still running — no sessionEndReason yet and recent activity). Stops
 * polling once the trajectory completes or goes stale.
 */

"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { Trajectory } from "@/lib/types";

/** Poll every 30 seconds while live */
const POLL_INTERVAL_MS = 30_000;

/**
 * If the last commit is older than this, the trajectory is considered dead
 * even without a sessionEndReason (handles abrupt kills where the
 * orchestrator didn't get to write final state).
 */
const STALE_THRESHOLD_MS = 3 * 60_000;

/** Whether a trajectory is still in progress */
export function isLiveTrajectory(trajectory: Trajectory): boolean {
  if (trajectory.sessionEndReason) {
    return false;
  }

  const lastCommit = trajectory.commits[trajectory.commits.length - 1];
  if (!lastCommit) {
    return false;
  }

  const lastActivityMs = new Date(lastCommit.timestamp).getTime();
  if (isNaN(lastActivityMs)) {
    return false;
  }

  return Date.now() - lastActivityMs < STALE_THRESHOLD_MS;
}

/**
 * Returns the latest trajectory data, polling every 30 seconds while live.
 * Liveness is determined by both sessionEndReason and data freshness —
 * a trajectory that stops producing commits for 3 minutes is considered dead.
 */
export function useLiveTrajectory(initial: Trajectory): {
  trajectory: Trajectory;
  isLive: boolean;
  lastRefreshed: Date;
} {
  const [trajectory, setTrajectory] = useState(initial);
  const [lastRefreshed, setLastRefreshed] = useState(() => new Date());
  const [isLive, setIsLive] = useState(() => isLiveTrajectory(initial));
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(
        `/api/trajectories/${encodeURIComponent(initial.id)}?bust=${Date.now()}`,
      );
      if (!response.ok) {
        return;
      }
      const data: Trajectory = await response.json();
      setTrajectory(data);
      setLastRefreshed(new Date());
      setIsLive(isLiveTrajectory(data));
    } catch {
      /** Network errors are transient — silently retry on next interval */
    }
  }, [initial.id]);

  /** Re-evaluate liveness on every poll tick (even without new data,
   *  the wall clock advances so a stale trajectory will flip to not-live) */
  useEffect(() => {
    if (!isLive) {
      return;
    }

    intervalRef.current = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== undefined) {
        clearInterval(intervalRef.current);
      }
    };
  }, [isLive, refresh]);

  return { trajectory, isLive, lastRefreshed };
}
