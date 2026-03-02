/**
 * Setups Client — wraps SetupCompare with full-data fetching.
 * Fetches all trajectory data (with commit histories) on mount,
 * then renders SetupCompare once loaded.
 */

"use client";

import { useState, useEffect, useRef } from "react";
import type { Trajectory } from "@/lib/types";
import { SetupCompare } from "@/components/compare/setup-compare";

type SetupsClientProps = {
  /** Summary-level trajectories from the server (fallback while loading) */
  allTraces: Trajectory[];
};

export function SetupsClient({ allTraces }: SetupsClientProps) {
  const [fullTraces, setFullTraces] = useState<Trajectory[]>([]);
  const [loading, setLoading] = useState(false);
  const fetched = useRef(false);

  useEffect(() => {
    if (fetched.current || loading) {
      return;
    }

    setLoading(true);
    fetch("/api/compare")
      .then((res) => res.json())
      .then((data: Trajectory[]) => {
        const active = data.filter((trace) => trace.finalPassed > 0);
        setFullTraces(active);
        fetched.current = true;
      })
      .catch(() => {})
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <span className="text-[12px] text-envoi-text-muted">
          Loading trajectory data...
        </span>
      </div>
    );
  }

  return (
    <SetupCompare
      allTraces={fullTraces.length > 0 ? fullTraces : allTraces}
    />
  );
}
