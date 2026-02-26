/**
 * Trajectories listing page — server component.
 * Shows all trajectories grouped by model, linking to detail pages.
 */

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { generateAllTrajectories } from "@/lib/mock";
import { TOTAL_TESTS } from "@/lib/constants";
import { formatPercent, formatDuration } from "@/lib/utils";

export default function TrajectoryListPage() {
  const allTraces = generateAllTrajectories();

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
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4 py-[10px]">
        <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          All Trajectories ({allTraces.length})
        </span>
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
              const pct = (finalPassed / TOTAL_TESTS) * 100;

              return (
                <Link
                  key={trace.id}
                  href={`/trajectory/${trace.id}`}
                  className="flex items-center gap-3 border-b border-envoi-border-light px-[14px] py-[10px] transition-colors hover:bg-envoi-surface"
                >
                  {/* ID */}
                  <span className="min-w-[90px] truncate text-[11px] font-medium text-envoi-text">
                    {trace.id}
                  </span>

                  {/* Params */}
                  <span className="min-w-[50px] text-[10px] text-envoi-text-dim">
                    {trace.params.implLang}
                  </span>
                  <span className="min-w-[50px] text-[10px] text-envoi-text-dim">
                    {trace.params.sandbox}
                  </span>
                  <span className="min-w-[60px] text-[10px] text-envoi-text-dim">
                    {trace.params.agent}
                  </span>

                  {/* Duration */}
                  <span className="min-w-[70px] text-[10px] text-envoi-text-muted">
                    {formatDuration(lastCommit?.minutesElapsed ?? 0)}
                  </span>

                  {/* Progress bar */}
                  <div className="flex flex-1 items-center gap-2">
                    <div className="h-[4px] w-[100px] rounded-full bg-envoi-border-light">
                      <div
                        className="h-full rounded-full bg-envoi-accent"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="text-[11px] font-semibold text-envoi-text">
                      {finalPassed}
                    </span>
                    <span className="text-[9px] text-envoi-text-dim">
                      {formatPercent(finalPassed, TOTAL_TESTS)}
                    </span>
                  </div>

                  {/* Commits count */}
                  <span className="min-w-[60px] text-right text-[10px] text-envoi-text-dim">
                    {trace.commits.length} commits
                  </span>

                  {/* Arrow */}
                  <ArrowUpRight size={12} className="shrink-0 text-envoi-text-dim" />
                </Link>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
