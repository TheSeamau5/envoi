/**
 * Milestone Divergence Table — shows when each selected trace reached key milestones.
 * Server-renderable: receives pre-computed data, no client state needed.
 *
 * Rows = milestones, Columns = selected traces.
 * Each cell: commit index + time when reached. Star icon for fastest. Spread column at right.
 */

import { Star, Minus } from "lucide-react";
import type { Trajectory, MilestoneDef, Commit } from "@/lib/types";
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

/** Compute the spread (max - min elapsed time) among traces that reached a milestone */
function computeSpread(hits: MilestoneHit[]): number | undefined {
  const times = hits
    .filter((hit) => hit.commit !== undefined)
    .map((hit) => hit.commit!.minutesElapsed);
  if (times.length < 2) return undefined;
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  return maxTime - minTime;
}

/** Find which trace index reached the milestone fastest */
function findFastestIndex(hits: MilestoneHit[]): number | undefined {
  const reachedHits = hits.filter((hit) => hit.commit !== undefined);
  if (reachedHits.length === 0) return undefined;
  const best = reachedHits.reduce((acc, hit) =>
    hit.commit!.minutesElapsed < acc.commit!.minutesElapsed ? hit : acc,
  );
  return best.traceIndex;
}

export function MilestoneTable({ traces }: MilestoneTableProps) {
  return (
    <div className="rounded border border-envoi-border bg-envoi-bg">
      {/* Header */}
      <div className="flex items-center border-b border-envoi-border bg-envoi-surface px-[14px] py-[10px]">
        <span className="min-w-[140px] text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Milestone
        </span>
        {traces.map((_trace, traceIndex) => {
          const color = TRACE_COLORS[traceIndex % TRACE_COLORS.length]!;
          return (
            <span
              key={`hdr-${traceIndex}`}
              className="flex min-w-[120px] flex-1 items-center gap-[6px] text-[10px] font-semibold uppercase tracking-[0.08em]"
              style={{ color: color.line }}
            >
              <span
                className="flex h-[16px] w-[16px] items-center justify-center rounded text-[9px] font-bold text-white"
                style={{ background: color.line }}
              >
                {color.label}
              </span>
              Trace {color.label}
            </span>
          );
        })}
        <span className="min-w-[80px] text-right text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Spread
        </span>
      </div>

      {/* Rows */}
      {MILESTONES.map((milestone: MilestoneDef) => {
        const hits: MilestoneHit[] = traces.map((trace, traceIndex) => ({
          commit: findMilestone(trace, milestone),
          traceIndex,
        }));
        const fastestIndex = findFastestIndex(hits);
        const spread = computeSpread(hits);

        return (
          <div
            key={milestone.id}
            className="flex items-center border-b border-envoi-border-light px-[14px] py-[10px] transition-colors hover:bg-envoi-surface"
          >
            {/* Milestone label */}
            <span className="min-w-[140px] text-[11px] font-medium text-envoi-text">
              {milestone.label}
            </span>

            {/* Per-trace cells */}
            {hits.map((hit) => {
              const color = TRACE_COLORS[hit.traceIndex % TRACE_COLORS.length]!;
              const isFastest = hit.traceIndex === fastestIndex;

              if (!hit.commit) {
                return (
                  <span
                    key={`cell-${milestone.id}-${hit.traceIndex}`}
                    className="flex min-w-[120px] flex-1 items-center gap-[4px] text-[10px] text-envoi-text-dim"
                  >
                    <Minus size={10} />
                    not reached
                  </span>
                );
              }

              return (
                <span
                  key={`cell-${milestone.id}-${hit.traceIndex}`}
                  className="flex min-w-[120px] flex-1 items-center gap-[6px]"
                >
                  <span className="text-[11px] font-semibold" style={{ color: color.line }}>
                    #{hit.commit.index}
                  </span>
                  <span className="text-[10px] text-envoi-text-muted">
                    {formatDuration(hit.commit.minutesElapsed)}
                  </span>
                  {isFastest && (
                    <Star
                      size={10}
                      fill={T.gold}
                      style={{ color: T.gold }}
                    />
                  )}
                </span>
              );
            })}

            {/* Spread */}
            <span className="min-w-[80px] text-right text-[11px] font-medium text-envoi-text-muted">
              {spread !== undefined ? formatDuration(spread) : (
                <Minus size={10} className="ml-auto text-envoi-text-dim" />
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
