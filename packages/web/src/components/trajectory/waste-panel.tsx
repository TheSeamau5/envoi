/**
 * Waste Audit panel — shows per-trajectory waste breakdown.
 * Client component — fetches waste data on mount via API.
 */

"use client";

import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import type { WasteEntry } from "@/lib/types";
import { T } from "@/lib/tokens";

type WastePanelProps = {
  trajectoryId: string;
};

/** Colors for waste categories */
const WASTE_COLORS: Record<string, string> = {
  redundant_read: T.red,
  expired_content: T.accent,
  repeated_error: T.gold,
  useless_exploration: T.textDim,
};

/** Human-readable labels for waste categories */
const WASTE_LABELS: Record<string, string> = {
  redundant_read: "Redundant Reads",
  expired_content: "Expired Content",
  repeated_error: "Repeated Errors",
  useless_exploration: "Useless Exploration",
};

export function WastePanel({ trajectoryId }: WastePanelProps) {
  const [entries, setEntries] = useState<WasteEntry[]>();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function fetchWaste() {
      try {
        const response = await fetch(
          `/api/trajectories/${encodeURIComponent(trajectoryId)}/waste`,
        );
        if (!response.ok) {
          return;
        }
        const data: WasteEntry[] = await response.json();
        if (!cancelled) {
          setEntries(data);
        }
      } catch {
        /** Waste data is optional — silently ignore */
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    fetchWaste();
    return () => {
      cancelled = true;
    };
  }, [trajectoryId]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Loader2 size={16} className="animate-spin text-envoi-text-dim" />
      </div>
    );
  }

  if (!entries || entries.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-[11px] text-envoi-text-dim">
        No waste detected
      </div>
    );
  }

  const totalWaste = entries.reduce((sum, entry) => sum + entry.tokensCost, 0);
  const totalPct = entries.reduce((sum, entry) => sum + entry.percentage, 0);

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      {/* Summary banner */}
      <div
        className="sticky top-0 z-10 flex items-center gap-[10px] border-b border-envoi-border px-[14px] py-[8px]"
        style={{ background: T.surface }}
      >
        <span className="text-[10px] font-semibold text-envoi-text">
          {totalWaste.toLocaleString()} tokens wasted
        </span>
        <span className="text-[9px] text-envoi-text-dim">
          ({totalPct.toFixed(1)}% of trajectory)
        </span>
      </div>

      {/* Waste entries */}
      <div className="flex flex-col gap-[2px] px-[14px] py-[10px]">
        {entries.map((entry) => {
          const color = WASTE_COLORS[entry.category] ?? T.textDim;
          const label = WASTE_LABELS[entry.category] ?? entry.category;
          const barWidth = totalWaste > 0
            ? Math.max(4, (entry.tokensCost / totalWaste) * 100)
            : 0;

          return (
            <div key={entry.category} className="flex flex-col gap-[4px] py-[8px]">
              {/* Label row */}
              <div className="flex items-center gap-[8px]">
                <div
                  className="h-[8px] w-[8px] shrink-0 rounded-full"
                  style={{ background: color }}
                />
                <span className="flex-1 text-[10px] font-semibold text-envoi-text">
                  {label}
                </span>
                <span className="text-[9px] text-envoi-text-dim">
                  {entry.count} occurrences
                </span>
              </div>

              {/* Bar */}
              <div className="flex items-center gap-[8px]">
                <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-envoi-border-light">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${barWidth}%`,
                      background: color,
                    }}
                  />
                </div>
                <span className="min-w-[60px] text-right text-[9px] text-envoi-text-muted">
                  {entry.tokensCost.toLocaleString()} tok
                </span>
                <span className="min-w-[36px] text-right text-[9px] text-envoi-text-dim">
                  {entry.percentage.toFixed(1)}%
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
