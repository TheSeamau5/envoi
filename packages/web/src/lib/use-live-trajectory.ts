/**
 * Hook that polls for fresh trajectory data when the trajectory is live.
 *
 * Liveness is determined by querying the sandbox provider (Modal / E2B) via
 * the /api/trajectories/:id/sandbox-status endpoint.  This is the source of
 * truth — no timestamp heuristics needed.
 *
 * If sessionEndReason is already present the trajectory is definitely done
 * and we skip the sandbox check entirely.
 *
 * The useEffect here starts the async poll chain on mount and cleans up
 * on unmount. No synchronous setState in the effect body — all state
 * updates happen inside async callbacks (fetch .then, setTimeout).
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
  const [trajectory, setTrajectory] = useState(initial);
  const [lastRefreshed, setLastRefreshed] = useState(() => new Date());
  const [live, setLive] = useState(() => isPossiblyLive(initial));

  /** Track mounted state for safe async setState */
  const mountedRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  /** Ref indirection so the effect always calls the latest refresh */
  const refreshRef = useRef<() => Promise<void>>(async () => {});

  const refresh = useCallback(async () => {
    try {
      /** Fetch latest trajectory data */
      const response = await fetch(
        `/api/trajectories/${encodeURIComponent(initial.id)}?project=${encodeURIComponent(project)}&bust=${Date.now()}`,
      );
      if (!response.ok || !mountedRef.current) {
        return;
      }
      const data: Trajectory = await response.json();
      if (!mountedRef.current) {
        return;
      }
      setTrajectory(data);
      setLastRefreshed(new Date());

      /** If sessionEndReason appeared, we're done */
      if (data.sessionEndReason) {
        setLive(false);
        return;
      }

      /** Ask the sandbox provider if it's still running */
      const running = await checkSandboxStatus(initial.id, project);
      if (!mountedRef.current) {
        return;
      }
      setLive(running);

      /** Schedule next poll only if still running */
      if (running) {
        timerRef.current = setTimeout(
          () => void refreshRef.current(),
          POLL_INTERVAL_MS,
        );
      }
    } catch {
      /** Network errors are transient — schedule retry if still mounted */
      if (mountedRef.current) {
        timerRef.current = setTimeout(
          () => void refreshRef.current(),
          POLL_INTERVAL_MS,
        );
      }
    }
  }, [initial.id, project]);

  refreshRef.current = refresh;

  /**
   * Start poll chain on mount, clean up on unmount.
   * The effect body has ZERO synchronous setState calls — it only calls
   * void refreshRef.current() which is async. All setState happens in
   * the async fetch callbacks, not in the effect body.
   * Survives React Strict Mode: cleanup kills the timer, re-fire restarts it.
   */
  useEffect(() => {
    mountedRef.current = true;

    if (isPossiblyLive(initial)) {
      void refreshRef.current();
    }

    return () => {
      mountedRef.current = false;
      if (timerRef.current !== undefined) {
        clearTimeout(timerRef.current);
      }
    };
    // mount-once: initial and refreshRef are stable
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { trajectory, isLive: live, lastRefreshed };
}
