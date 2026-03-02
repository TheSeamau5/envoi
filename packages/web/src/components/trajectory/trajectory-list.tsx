/**
 * Trajectory list — client component with active/failed tabs.
 * Groups trajectories by environment (first level) then model (second level),
 * sorted in reverse chronological order within each group.
 * Failed runs (best score = 0) are shown in a separate tab.
 */

"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { Trajectory } from "@/lib/types";
import { TOTAL_TESTS, computeTotalTests } from "@/lib/constants";
import { formatPercent, formatDate, needsYear } from "@/lib/utils";

type TrajectoryListProps = {
  trajectories: Trajectory[];
};

type Tab = "active" | "failed";

/** Fixed column widths — shared between header and rows for alignment */
const COL = {
  id: "w-[260px] shrink-0",
  target: "w-[64px] shrink-0",
  lang: "w-[48px] shrink-0",
  nl: "w-[40px] shrink-0",
  started: "w-35 shrink-0",
  duration: "w-[80px] shrink-0",
  score: "flex-1 min-w-[200px]",
  evals: "w-[72px] shrink-0",
} as const;

const HEADER_STYLE = "text-[13px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim whitespace-nowrap";
const CELL_BORDER = "border-r border-envoi-border-light px-3";

type GroupedTrajectories = Map<string, Map<string, Trajectory[]>>;

/** Group trajectories by environment then model, sorted reverse chronological */
function groupByEnvironmentThenModel(traces: Trajectory[]): GroupedTrajectories {
  const envGroups: GroupedTrajectories = new Map();

  for (const trace of traces) {
    const environment = trace.environment || "unknown";
    let modelMap = envGroups.get(environment);
    if (!modelMap) {
      modelMap = new Map<string, Trajectory[]>();
      envGroups.set(environment, modelMap);
    }
    const modelTraces = modelMap.get(trace.model);
    if (modelTraces) {
      modelTraces.push(trace);
    } else {
      modelMap.set(trace.model, [trace]);
    }
  }

  /** Sort traces within each model group by startedAt DESC */
  for (const modelMap of envGroups.values()) {
    for (const traces of modelMap.values()) {
      traces.sort((traceA, traceB) => {
        const dateA = new Date(traceA.startedAt).getTime();
        const dateB = new Date(traceB.startedAt).getTime();
        return dateB - dateA;
      });
    }
  }

  /** Sort environment keys alphabetically */
  const sorted: GroupedTrajectories = new Map(
    [...envGroups.entries()].sort(([envA], [envB]) => envA.localeCompare(envB)),
  );

  return sorted;
}

export function TrajectoryList({ trajectories }: TrajectoryListProps) {
  const [activeTab, setActiveTab] = useState<Tab>("active");

  const { activeTraces, failedTraces } = useMemo(() => {
    const active: Trajectory[] = [];
    const failed: Trajectory[] = [];
    for (const trace of trajectories) {
      const hasScore = trace.finalPassed > 0;
      const hasSubstantialWork = trace.commits.length >= 3;
      if (hasScore || hasSubstantialWork) {
        active.push(trace);
      } else {
        failed.push(trace);
      }
    }
    return { activeTraces: active, failedTraces: failed };
  }, [trajectories]);

  const currentTraces = activeTab === "active" ? activeTraces : failedTraces;
  const grouped = useMemo(() => groupByEnvironmentThenModel(currentTraces), [currentTraces]);
  const showYear = useMemo(() => needsYear(currentTraces.map((trace) => trace.startedAt)), [currentTraces]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Header with tabs */}
      <div className="flex h-10.25 shrink-0 items-center gap-4 border-b border-envoi-border bg-envoi-bg px-4">
        <button
          type="button"
          onClick={() => setActiveTab("active")}
          className={`text-[12px] font-semibold uppercase tracking-[0.08em] transition-colors ${
            activeTab === "active"
              ? "text-envoi-text"
              : "text-envoi-text-dim hover:text-envoi-text-muted"
          }`}
        >
          Active ({activeTraces.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("failed")}
          className={`text-[12px] font-semibold uppercase tracking-[0.08em] transition-colors ${
            activeTab === "failed"
              ? "text-envoi-text"
              : "text-envoi-text-dim hover:text-envoi-text-muted"
          }`}
        >
          Failed ({failedTraces.length})
        </button>
      </div>

      {/* Column header */}
      <div className="flex shrink-0 items-center border-b border-envoi-border bg-envoi-surface px-3.5 py-1.5">
        <span className={`${COL.id} ${CELL_BORDER} ${HEADER_STYLE} pl-0`}>ID</span>
        <span className={`${COL.target} ${CELL_BORDER} ${HEADER_STYLE}`}>Target</span>
        <span className={`${COL.lang} ${CELL_BORDER} ${HEADER_STYLE}`}>Lang</span>
        <span className={`${COL.nl} ${CELL_BORDER} ${HEADER_STYLE}`}>NL</span>
        <span className={`${COL.started} ${CELL_BORDER} ${HEADER_STYLE}`}>Started</span>
        <span className={`${COL.duration} ${CELL_BORDER} ${HEADER_STYLE}`}>Duration</span>
        <span className={`${COL.score} ${CELL_BORDER} ${HEADER_STYLE}`}>Score</span>
        <span className={`${COL.evals} px-3 text-right ${HEADER_STYLE}`}>Evals</span>
        <span className="w-3 shrink-0" />
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        {currentTraces.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-[13px] text-envoi-text-dim">
            {activeTab === "active" ? "No active trajectories" : "No failed trajectories"}
          </div>
        ) : (
          [...grouped.entries()].map(([environment, modelMap]) => (
            <div key={environment}>
              {/* Environment group header (first level) */}
              <div className="border-b border-envoi-border bg-envoi-bg px-3.5 py-3">
                <span className="text-[13px] font-bold uppercase tracking-[0.08em] text-envoi-text">
                  {environment}
                </span>
                <span className="ml-2 text-[12px] text-envoi-text-dim">
                  ({[...modelMap.values()].reduce((sum, traces) => sum + traces.length, 0)} trajectories)
                </span>
              </div>

              {[...modelMap.entries()].map(([model, traces]) => (
                <div key={`${environment}-${model}`}>
                  {/* Model group header (second level) */}
                  <div className="border-b border-envoi-border bg-envoi-surface px-3.5 py-2.5 pl-6">
                    <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
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
                    const evalCount = lastCommit?.evalId ? Number(lastCommit.evalId) : 0;

                    return (
                      <Link
                        key={trace.id}
                        href={`/trajectory/${trace.id}`}
                        className="flex items-center border-b border-envoi-border-light px-3.5 py-2.5 transition-colors hover:bg-envoi-surface"
                      >
                        {/* ID */}
                        <span className={`${COL.id} ${CELL_BORDER} truncate pl-0 text-[13px] font-medium text-envoi-text`}>
                          {trace.id}
                        </span>

                        {/* Target */}
                        <span className={`${COL.target} ${CELL_BORDER} truncate text-[12px] text-envoi-text-dim`}>
                          {(trace.params.target ?? "").split("-")[0]}
                        </span>

                        {/* Impl Language */}
                        <span className={`${COL.lang} ${CELL_BORDER} truncate text-[12px] text-envoi-text-dim`}>
                          {trace.params.implLang ?? ""}
                        </span>

                        {/* Natural Language */}
                        <span className={`${COL.nl} ${CELL_BORDER} truncate text-[12px] text-envoi-text-dim`}>
                          {trace.params.lang ?? ""}
                        </span>

                        {/* Date started */}
                        <span className={`${COL.started} ${CELL_BORDER} whitespace-nowrap font-mono text-[12px] text-envoi-text-muted`}>
                          {formatDate(trace.startedAt, showYear)}
                        </span>

                        {/* Duration */}
                        <span className={`${COL.duration} ${CELL_BORDER} whitespace-nowrap text-[12px] text-envoi-text-muted`}>
                          {trace.duration || "—"}
                        </span>

                        {/* Progress bar + score */}
                        <div className={`${COL.score} ${CELL_BORDER} flex items-center gap-2`}>
                          <div className="h-1 w-35 shrink-0 rounded-full bg-envoi-border-light">
                            <div
                              className="h-full rounded-full bg-envoi-accent"
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className="whitespace-nowrap text-[13px] font-semibold text-envoi-text">
                            {finalPassed}
                          </span>
                          <span className="whitespace-nowrap text-[13px] text-envoi-text-dim">
                            {formatPercent(finalPassed, totalTests)}
                          </span>
                        </div>

                        {/* Eval count */}
                        <span className={`${COL.evals} whitespace-nowrap px-3 text-right text-[12px] text-envoi-text-dim`}>
                          {evalCount > 0 ? `${evalCount} evals` : "—"}
                        </span>

                        {/* Arrow */}
                        <ArrowUpRight size={12} className="w-3 shrink-0 text-envoi-text-dim" />
                      </Link>
                    );
                  })}
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
