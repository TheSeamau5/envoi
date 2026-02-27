/**
 * Milestone Divergence Table — shows when each selected trace reached key milestones.
 * Server-renderable: receives pre-computed data, no client state needed.
 *
 * Transposed layout: Rows = traces (observations), Columns = milestones.
 * First column (trace labels) is sticky so it stays visible during horizontal scroll.
 * Each cell: commit index + time when reached. Star icon for fastest. Spread row at bottom.
 */

import { Star, Minus } from "lucide-react";
import type { Trajectory, Commit } from "@/lib/types";
import { TRACE_COLORS, T } from "@/lib/tokens";
import { MILESTONES } from "@/lib/constants";
import { findMilestone, formatDuration } from "@/lib/utils";

type MilestoneTableProps = {
  traces: Trajectory[];
};

/** Result of looking up a milestone for one trace */
type MilestoneHit = {
  commit: Commit | undefined;
  traceIndex: number;
};

/** Find which trace index reached the milestone fastest */
function findFastestIndex(hits: MilestoneHit[]): number | undefined {
  const reachedHits = hits.filter((hit) => hit.commit !== undefined);
  if (reachedHits.length === 0) return undefined;
  const best = reachedHits.reduce((acc, hit) =>
    hit.commit!.minutesElapsed < acc.commit!.minutesElapsed ? hit : acc,
  );
  return best.traceIndex;
}

/** Compute the spread (max - min elapsed time) among traces that reached a milestone */
function computeSpread(hits: MilestoneHit[]): number | undefined {
  const times = hits
    .filter((hit) => hit.commit !== undefined)
    .map((hit) => hit.commit!.minutesElapsed);
  if (times.length < 2) return undefined;
  return Math.max(...times) - Math.min(...times);
}

/** Pre-compute all milestone hits for all traces */
function buildHitsGrid(traces: Trajectory[]) {
  return MILESTONES.map((milestone) => {
    const hits = traces.map((trace, traceIndex) => ({
      commit: findMilestone(trace, milestone),
      traceIndex,
    }));
    return { milestone, hits, fastest: findFastestIndex(hits), spread: computeSpread(hits) };
  });
}

export function MilestoneTable({ traces }: MilestoneTableProps) {
  const grid = buildHitsGrid(traces);

  return (
    <div className="rounded border border-envoi-border bg-envoi-bg">
      {/* Scrollable container */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse" style={{ minWidth: "max-content" }}>
          <thead>
            <tr className="bg-envoi-surface">
              {/* Sticky first column: "Trace" header */}
              <th
                className="sticky left-0 z-10 min-w-[140px] border-b border-r border-envoi-border bg-envoi-surface px-[14px] py-[10px] text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim"
              >
                Trace
              </th>
              {/* Milestone column headers */}
              {grid.map(({ milestone }) => (
                <th
                  key={milestone.id}
                  className="border-b border-envoi-border px-[14px] py-[10px] text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim whitespace-nowrap"
                >
                  {milestone.label}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {/* One row per trace */}
            {traces.map((trace, traceIndex) => {
              const color = TRACE_COLORS[traceIndex % TRACE_COLORS.length]!;
              return (
                <tr key={trace.id} className="transition-colors hover:bg-envoi-surface">
                  {/* Sticky trace label */}
                  <td
                    className="sticky left-0 z-10 border-b border-r border-envoi-border bg-envoi-bg px-[14px] py-[10px]"
                  >
                    <span className="flex items-center gap-[6px]">
                      <span
                        className="flex h-[16px] w-[16px] shrink-0 items-center justify-center rounded text-[9px] font-bold text-white"
                        style={{ background: color.line }}
                      >
                        {color.label}
                      </span>
                      <span className="text-[11px] font-medium" style={{ color: color.line }}>
                        Trace {color.label}
                      </span>
                    </span>
                  </td>

                  {/* Milestone cells */}
                  {grid.map(({ milestone, hits, fastest }) => {
                    const hit = hits[traceIndex]!;
                    const isFastest = traceIndex === fastest;

                    return (
                      <td
                        key={`${trace.id}-${milestone.id}`}
                        className="border-b border-envoi-border-light px-[14px] py-[10px] whitespace-nowrap"
                      >
                        {hit.commit ? (
                          <span className="flex items-center gap-[6px]">
                            <span className="text-[11px] font-semibold" style={{ color: color.line }}>
                              #{hit.commit.index}
                            </span>
                            <span className="text-[10px] text-envoi-text-muted">
                              {formatDuration(hit.commit.minutesElapsed)}
                            </span>
                            {isFastest && (
                              <Star size={10} fill={T.gold} style={{ color: T.gold }} />
                            )}
                          </span>
                        ) : (
                          <span className="flex items-center gap-[4px] text-[10px] text-envoi-text-dim">
                            <Minus size={10} />
                            not reached
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}

            {/* Spread row */}
            <tr className="bg-envoi-surface">
              <td className="sticky left-0 z-10 border-r border-envoi-border bg-envoi-surface px-[14px] py-[10px] text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                Spread
              </td>
              {grid.map(({ milestone, spread }) => (
                <td
                  key={`spread-${milestone.id}`}
                  className="px-[14px] py-[10px] whitespace-nowrap"
                >
                  {spread !== undefined ? (
                    <span className="text-[11px] font-medium text-envoi-text-muted">
                      {formatDuration(spread)}
                    </span>
                  ) : (
                    <Minus size={10} className="text-envoi-text-dim" />
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
