/**
 * Setups Client — wraps SetupCompare with full-data fetching.
 * Fetches all trajectory data (with commit histories) on mount,
 * then renders SetupCompare once loaded.
 *
 * ZERO useEffect — fetch is ref-guarded in the render body;
 * setState happens in async .then callbacks, not in effects.
 */

"use client";

import { useState, useRef } from "react";
import type { Trajectory } from "@/lib/types";
import { SetupCompare } from "@/components/compare/setup-compare";

type SetupsClientProps = {
  /** Summary-level trajectories from the server (fallback while loading) */
  allTraces: Trajectory[];
  project: string;
};

export function SetupsClient({ allTraces, project }: SetupsClientProps) {
  const [fullTraces, setFullTraces] = useState<Trajectory[]>([]);
  const [loading, setLoading] = useState(true);
  const fetchRef = useRef(false);

  /** Fire fetch exactly once — guarded by ref, not driven by useEffect */
  if (!fetchRef.current) {
    fetchRef.current = true;
    fetch(`/api/compare?project=${encodeURIComponent(project)}`)
      .then((res) => res.json())
      .then((data: Trajectory[]) => {
        const active = data.filter((trace) => trace.finalPassed > 0);
        setFullTraces(active);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }

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
    <SetupCompare allTraces={fullTraces.length > 0 ? fullTraces : allTraces} />
  );
}
