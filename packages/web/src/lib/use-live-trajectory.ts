/**
 * Hook that polls for fresh trajectory data when the trajectory is live.
 *
 * Liveness is determined by querying the sandbox provider (Modal / E2B) via
 * the /api/trajectories/:id/sandbox-status endpoint.  This is the source of
 * truth — no timestamp heuristics needed.
 *
 * If sessionEndReason is already present the trajectory is definitely done
 * and we skip the sandbox check entirely.
 */

"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { Trajectory } from "@/lib/types";

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
async function checkSandboxStatus(trajectoryId: string): Promise<boolean> {
  try {
    const response = await fetch(
      `/api/trajectories/${encodeURIComponent(trajectoryId)}/sandbox-status`,
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
export function useLiveTrajectory(initial: Trajectory): {
  trajectory: Trajectory;
  isLive: boolean;
  lastRefreshed: Date;
} {
  const [trajectory, setTrajectory] = useState(initial);
  const [lastRefreshed, setLastRefreshed] = useState(() => new Date());
  const [live, setLive] = useState(() => isPossiblyLive(initial));
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      /** Fetch latest trajectory data */
      const response = await fetch(
        `/api/trajectories/${encodeURIComponent(initial.id)}?bust=${Date.now()}`,
      );
      if (!response.ok) {
        return;
      }
      const data: Trajectory = await response.json();
      setTrajectory(data);
      setLastRefreshed(new Date());

      /** If sessionEndReason appeared, we're done */
      if (data.sessionEndReason) {
        setLive(false);
        return;
      }

      /** Ask the sandbox provider if it's still running */
      const running = await checkSandboxStatus(initial.id);
      setLive(running);
    } catch {
      /** Network errors are transient — silently retry on next interval */
    }
  }, [initial.id]);

  /** Immediate fresh fetch on mount — server data may be stale from cache */
  useEffect(() => {
    refresh();
  }, [refresh]);

  /** Poll every 30s while live */
  useEffect(() => {
    if (!live) {
      return;
    }

    intervalRef.current = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== undefined) {
        clearInterval(intervalRef.current);
      }
    };
  }, [live, refresh]);

  return { trajectory, isLive: live, lastRefreshed };
}
