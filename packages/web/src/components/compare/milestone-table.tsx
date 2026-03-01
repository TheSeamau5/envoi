/**
 * Milestone Divergence Table — shows when each selected trace reached key milestones.
 * Server-renderable: receives pre-computed data, no client state needed.
 *
 * Transposed layout: Rows = traces (observations), Columns = milestones grouped by suite.
 * First column (trace labels) is sticky with a right border.
 * Suite group headers span their columns with a right border separator after each group.
 * Each cell: commit index + time when reached. Star icon for fastest. Spread row at bottom.
 */

import { Star, Minus } from "lucide-react";
import type { Trajectory, Commit, Suite, MilestoneDef } from "@/lib/types";
import { TRACE_COLORS, T } from "@/lib/tokens";
import { MILESTONES as DEFAULT_MILESTONES, SUITES as DEFAULT_SUITES, computeMilestones } from "@/lib/constants";
import { findMilestone, formatDuration } from "@/lib/utils";

type MilestoneTableProps = {
  traces: Trajectory[];
  /** Stable color index for each trace (parallel to `traces` array) */
  colorIndices?: number[];
  suites?: Suite[];
};

/** Result of looking up a milestone for one trace */
type MilestoneHit = {
  commit: Commit | undefined;
  traceIndex: number;
};

/** Type guard for hits with a defined commit */
function hasCommit(hit: MilestoneHit): hit is MilestoneHit & { commit: Commit } {
  return hit.commit !== undefined;
}

/** Find which trace index reached the milestone fastest */
function findFastestIndex(hits: MilestoneHit[]): number | undefined {
  const reachedHits = hits.filter(hasCommit);
  if (reachedHits.length === 0) return undefined;
  const best = reachedHits.reduce((acc, hit) =>
    hit.commit.minutesElapsed < acc.commit.minutesElapsed ? hit : acc,
  );
  return best.traceIndex;
}

/** Compute the spread (max - min elapsed time) among traces that reached a milestone */
function computeSpread(hits: MilestoneHit[]): number | undefined {
  const times = hits.filter(hasCommit).map((hit) => hit.commit.minutesElapsed);
  if (times.length < 2) return undefined;
  return Math.max(...times) - Math.min(...times);
}

/** Pre-compute all milestone hits for all traces */
function buildHitsGrid(traces: Trajectory[], milestones: MilestoneDef[]) {
  return milestones.map((milestone) => {
    const hits = traces.map((trace, traceIndex) => ({
      commit: findMilestone(trace, milestone),
      traceIndex,
    }));
    return { milestone, hits, fastest: findFastestIndex(hits), spread: computeSpread(hits) };
  });
}

/** Group milestones by their group field, preserving order */
function buildGroups(grid: ReturnType<typeof buildHitsGrid>) {
  const groups: { name: string; items: typeof grid }[] = [];
  for (const entry of grid) {
    const last = groups[groups.length - 1];
    if (last && last.name === entry.milestone.group) {
      last.items.push(entry);
    } else {
      groups.push({ name: entry.milestone.group, items: [entry] });
    }
  }
  return groups;
}

export function MilestoneTable({ traces, colorIndices, suites: suitesProp }: MilestoneTableProps) {
  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const milestones = suitesProp ? computeMilestones(effectiveSuites) : DEFAULT_MILESTONES;
  const grid = buildHitsGrid(traces, milestones);
  const groups = buildGroups(grid);

  return (
    <div className="rounded border border-envoi-border bg-envoi-bg">
      {/* Scrollable container */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse" style={{ minWidth: "max-content" }}>
          <thead>
            {/* Suite group header row */}
            <tr className="bg-envoi-surface">
              <th
                className="sticky left-0 z-10 min-w-[140px] border-b border-r border-envoi-border bg-envoi-surface px-[14px] py-[6px]"
                rowSpan={2}
              >
                <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                  Trace
                </span>
              </th>
              {groups.map((group, groupIdx) => (
                <th
                  key={group.name}
                  colSpan={group.items.length}
                  className={`border-b border-envoi-border px-[14px] py-[6px] text-center text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim ${
                    groupIdx < groups.length - 1 ? "border-r" : ""
                  }`}
                >
                  {group.name}
                </th>
              ))}
            </tr>
            {/* Milestone label row */}
            <tr className="bg-envoi-surface">
              {groups.map((group, groupIdx) =>
                group.items.map((entry, entryIdx) => {
                  const isLastInGroup = entryIdx === group.items.length - 1;
                  const isLastGroup = groupIdx === groups.length - 1;
                  return (
                    <th
                      key={entry.milestone.id}
                      className={`border-b border-envoi-border px-[14px] py-[6px] text-left text-[10px] font-medium whitespace-nowrap text-envoi-text-dim ${
                        isLastInGroup && !isLastGroup ? "border-r" : ""
                      }`}
                    >
                      {entry.milestone.label}
                    </th>
                  );
                }),
              )}
            </tr>
          </thead>

          <tbody>
            {/* One row per trace */}
            {traces.map((trace, traceIndex) => {
              const colorIdx = (colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length;
              const color = TRACE_COLORS[colorIdx];
              if (!color) return undefined;
              return (
                <tr key={trace.id} className="transition-colors hover:bg-envoi-surface">
                  {/* Sticky trace label */}
                  <td className="sticky left-0 z-10 border-b border-r border-envoi-border bg-envoi-bg px-[14px] py-[10px]">
                    <span className="flex items-center gap-[6px]">
                      <span
                        className="flex h-[16px] w-[16px] shrink-0 items-center justify-center rounded text-[9px] font-bold text-white"
                        style={{ background: color.line }}
                      >
                        {color.label}
                      </span>
                      <span className="text-[11px] font-medium whitespace-nowrap" style={{ color: color.line }}>
                        Trace {color.label}
                      </span>
                    </span>
                  </td>

                  {/* Milestone cells */}
                  {groups.map((group, groupIdx) =>
                    group.items.map((entry, entryIdx) => {
                      const hit = entry.hits[traceIndex];
                      if (!hit) return undefined;
                      const isFastest = traceIndex === entry.fastest;
                      const isLastInGroup = entryIdx === group.items.length - 1;
                      const isLastGroup = groupIdx === groups.length - 1;

                      return (
                        <td
                          key={`${trace.id}-${entry.milestone.id}`}
                          className={`border-b border-envoi-border-light px-[14px] py-[10px] whitespace-nowrap ${
                            isLastInGroup && !isLastGroup ? "border-r border-r-envoi-border" : ""
                          }`}
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
                            </span>
                          )}
                        </td>
                      );
                    }),
                  )}
                </tr>
              );
            })}

            {/* Spread row */}
            <tr className="bg-envoi-surface">
              <td className="sticky left-0 z-10 border-r border-envoi-border bg-envoi-surface px-[14px] py-[10px] text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                Spread
              </td>
              {groups.map((group, groupIdx) =>
                group.items.map((entry, entryIdx) => {
                  const isLastInGroup = entryIdx === group.items.length - 1;
                  const isLastGroup = groupIdx === groups.length - 1;
                  return (
                    <td
                      key={`spread-${entry.milestone.id}`}
                      className={`px-[14px] py-[10px] whitespace-nowrap ${
                        isLastInGroup && !isLastGroup ? "border-r border-r-envoi-border" : ""
                      }`}
                    >
                      {entry.spread !== undefined ? (
                        <span className="text-[11px] font-medium text-envoi-text-muted">
                          {formatDuration(entry.spread)}
                        </span>
                      ) : (
                        <Minus size={10} className="text-envoi-text-dim" />
                      )}
                    </td>
                  );
                }),
              )}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
