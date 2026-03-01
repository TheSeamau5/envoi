/**
 * Trajectories listing page — server component.
 * Shows all trajectories grouped by model, linking to detail pages.
 */

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { getAllTrajectories } from "@/lib/server/data";
import { TOTAL_TESTS, computeTotalTests } from "@/lib/constants";
import { formatPercent, formatDuration, formatDate } from "@/lib/utils";

/** Fixed column widths — shared between header and rows for alignment */
const COL = {
  id: "w-[260px] shrink-0",
  target: "w-[64px] shrink-0",
  lang: "w-[48px] shrink-0",
  nl: "w-[40px] shrink-0",
  started: "w-[140px] shrink-0",
  duration: "w-[64px] shrink-0",
  score: "flex-1 min-w-[200px]",
  commits: "w-[72px] shrink-0",
} as const;

const HEADER_STYLE = "text-[9px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim whitespace-nowrap";
const CELL_BORDER = "border-r border-envoi-border-light px-3";

export default async function TrajectoryListPage() {
  const allTraces = await getAllTrajectories();

  /** Group by model */
  const grouped = new Map<string, typeof allTraces>();
  for (const trace of allTraces) {
    const existing = grouped.get(trace.model);
    if (existing) {
      existing.push(trace);
    } else {
      grouped.set(trace.model, [trace]);
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex h-[41px] shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          All Trajectories ({allTraces.length})
        </span>
      </div>

      {/* Column header */}
      <div className="flex shrink-0 items-center border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px]">
        <span className={`${COL.id} ${CELL_BORDER} ${HEADER_STYLE} pl-0`}>ID</span>
        <span className={`${COL.target} ${CELL_BORDER} ${HEADER_STYLE}`}>Target</span>
        <span className={`${COL.lang} ${CELL_BORDER} ${HEADER_STYLE}`}>Lang</span>
        <span className={`${COL.nl} ${CELL_BORDER} ${HEADER_STYLE}`}>NL</span>
        <span className={`${COL.started} ${CELL_BORDER} ${HEADER_STYLE}`}>Started</span>
        <span className={`${COL.duration} ${CELL_BORDER} ${HEADER_STYLE}`}>Duration</span>
        <span className={`${COL.score} ${CELL_BORDER} ${HEADER_STYLE}`}>Score</span>
        <span className={`${COL.commits} px-3 text-right ${HEADER_STYLE}`}>Commits</span>
        <span className="w-[12px] shrink-0" />
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        {[...grouped.entries()].map(([model, traces]) => (
          <div key={model}>
            {/* Model group header */}
            <div className="border-b border-envoi-border bg-envoi-surface px-[14px] py-[10px]">
              <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                {model} ({traces.length})
              </span>
            </div>

            {/* Trace rows */}
            {traces.map((trace) => {
              const lastCommit = trace.commits[trace.commits.length - 1];
              const finalPassed = lastCommit?.totalPassed ?? 0;
              const totalTests = trace.suites
                ? computeTotalTests(trace.suites)
                : TOTAL_TESTS;
              const pct = totalTests > 0 ? (finalPassed / totalTests) * 100 : 0;

              return (
                <Link
                  key={trace.id}
                  href={`/trajectory/${trace.id}`}
                  className="flex items-center border-b border-envoi-border-light px-[14px] py-[10px] transition-colors hover:bg-envoi-surface"
                >
                  {/* ID */}
                  <span className={`${COL.id} ${CELL_BORDER} truncate pl-0 text-[11px] font-medium text-envoi-text`}>
                    {trace.id}
                  </span>

                  {/* Target */}
                  <span className={`${COL.target} ${CELL_BORDER} truncate text-[10px] text-envoi-text-dim`}>
                    {(trace.params.target ?? "").split("-")[0]}
                  </span>

                  {/* Impl Language */}
                  <span className={`${COL.lang} ${CELL_BORDER} truncate text-[10px] text-envoi-text-dim`}>
                    {trace.params.implLang ?? ""}
                  </span>

                  {/* Natural Language */}
                  <span className={`${COL.nl} ${CELL_BORDER} truncate text-[10px] text-envoi-text-dim`}>
                    {trace.params.lang ?? ""}
                  </span>

                  {/* Date started */}
                  <span className={`${COL.started} ${CELL_BORDER} whitespace-nowrap font-mono text-[10px] text-envoi-text-muted`}>
                    {formatDate(trace.startedAt)}
                  </span>

                  {/* Duration */}
                  <span className={`${COL.duration} ${CELL_BORDER} whitespace-nowrap text-[10px] text-envoi-text-muted`}>
                    {formatDuration(lastCommit?.minutesElapsed ?? 0)}
                  </span>

                  {/* Progress bar + score */}
                  <div className={`${COL.score} ${CELL_BORDER} flex items-center gap-2`}>
                    <div className="h-[4px] w-[140px] shrink-0 rounded-full bg-envoi-border-light">
                      <div
                        className="h-full rounded-full bg-envoi-accent"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="whitespace-nowrap text-[11px] font-semibold text-envoi-text">
                      {finalPassed}
                    </span>
                    <span className="whitespace-nowrap text-[9px] text-envoi-text-dim">
                      {formatPercent(finalPassed, totalTests)}
                    </span>
                  </div>

                  {/* Commits count */}
                  <span className={`${COL.commits} whitespace-nowrap px-3 text-right text-[10px] text-envoi-text-dim`}>
                    {trace.commits.length} commits
                  </span>

                  {/* Arrow */}
                  <ArrowUpRight size={12} className="w-[12px] shrink-0 text-envoi-text-dim" />
                </Link>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
