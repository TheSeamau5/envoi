/**
 * Trajectory list — grouped trajectory table with inline live state.
 * Uses client cache first so recent revisits render immediately.
 */

"use client";

import { useMemo } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import type { Trajectory } from "@/lib/types";
import { isPossiblyLive } from "@/lib/use-live-trajectory";
import { useProjectRevision } from "@/lib/use-project-revision";
import { TOTAL_TESTS, computeTotalTests } from "@/lib/constants";
import { PageHeader } from "@/components/page-shell";
import { TrajectoryListSkeleton } from "@/components/page-skeletons";
import { formatPercent, formatDate, needsYear } from "@/lib/utils";
import { queryKeys } from "@/lib/query-keys";

type TrajectoryListProps = {
  trajectories: Trajectory[];
  project: string;
};

type TrajectoryListQueryData = {
  trajectories: Trajectory[];
  liveIds: Set<string>;
};

function normalizeTrajectoryListData(value: unknown): TrajectoryListQueryData {
  if (
    value !== null &&
    typeof value === "object" &&
    "trajectories" in value &&
    Array.isArray(value.trajectories)
  ) {
    const liveIds =
      "liveIds" in value && value.liveIds instanceof Set
        ? value.liveIds
        : new Set<string>();
    return {
      trajectories: value.trajectories as Trajectory[],
      liveIds,
    };
  }
  return {
    trajectories: [],
    liveIds: new Set<string>(),
  };
}

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

const HEADER_STYLE =
  "text-[13px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim whitespace-nowrap";
const CELL_BORDER = "border-r border-envoi-border-light px-3";

type GroupedTrajectories = Map<string, Map<string, Trajectory[]>>;

/** Group trajectories by environment then model, sorted reverse chronological */
function groupByEnvironmentThenModel(
  traces: Trajectory[],
): GroupedTrajectories {
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

/** Poll every 30 seconds while any trajectory is live */
const LIST_POLL_MS = 30_000;

/**
 * Check sandbox status for a list of trajectory IDs.
 * Returns the set of IDs that are confirmed running.
 */
async function checkLiveTrajectories(
  trajectoryIds: string[],
  project: string,
): Promise<Set<string>> {
  const liveIds = new Set<string>();
  await Promise.allSettled(
    trajectoryIds.map(async (trajectoryId) => {
      const response = await fetch(
        `/api/trajectories/${encodeURIComponent(trajectoryId)}/sandbox-status?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        return;
      }
      const data = await response.json();
      if (data.running === true) {
        liveIds.add(trajectoryId);
      }
    }),
  );
  return liveIds;
}

export function TrajectoryList({
  trajectories: initialTrajectories,
  project,
}: TrajectoryListProps) {
  useProjectRevision(project, {
    invalidatePrefixes: [queryKeys.trajectories.all(project)],
  });

  /** Fetch trajectory list with polling */
  const trajectoriesQuery = useQuery({
    queryKey: queryKeys.trajectories.all(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/trajectories?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch trajectories");
      }
      const data: Trajectory[] = await response.json();
      return { trajectories: data, liveIds: new Set<string>() };
    },
    initialData:
      initialTrajectories.length > 0
        ? {
            trajectories: initialTrajectories,
            liveIds: new Set<string>(),
          }
        : undefined,
    staleTime: 0,
  });

  const normalizedQueryData = normalizeTrajectoryListData(
    trajectoriesQuery.data,
  );
  const trajectories =
    normalizedQueryData.trajectories.length > 0
      ? normalizedQueryData.trajectories
      : initialTrajectories;
  const candidateIds = useMemo(
    () =>
      trajectories
        .filter((trace) => isPossiblyLive(trace))
        .map((trace) => trace.id)
        .sort(),
    [trajectories],
  );

  const liveIdsQuery = useQuery({
    queryKey: queryKeys.trajectories.live(project, candidateIds),
    queryFn: () => checkLiveTrajectories(candidateIds, project),
    initialData: new Set<string>(),
    enabled: candidateIds.length > 0,
    refetchInterval: candidateIds.length > 0 ? LIST_POLL_MS : false,
    staleTime: 0,
  });

  const liveIds = liveIdsQuery.data ?? normalizedQueryData.liveIds;
  const grouped = useMemo(
    () => groupByEnvironmentThenModel(trajectories),
    [trajectories],
  );
  const showYear = useMemo(
    () => needsYear(trajectories.map((trace) => trace.startedAt)),
    [trajectories],
  );
  const showSkeleton =
    trajectories.length === 0 && trajectoriesQuery.isPending;

  if (showSkeleton) {
    return <TrajectoryListSkeleton />;
  }

  return (
    <div className="flex w-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader
        title="Trajectories"
        right={<span>{trajectories.length} total</span>}
      />

      {/* Column header */}
      <div className="flex shrink-0 items-center border-b border-envoi-border bg-envoi-surface px-3.5 py-1.5">
        <span className={`${COL.id} ${CELL_BORDER} ${HEADER_STYLE} pl-0`}>
          ID
        </span>
        <span className={`${COL.target} ${CELL_BORDER} ${HEADER_STYLE}`}>
          Target
        </span>
        <span className={`${COL.lang} ${CELL_BORDER} ${HEADER_STYLE}`}>
          Lang
        </span>
        <span className={`${COL.nl} ${CELL_BORDER} ${HEADER_STYLE}`}>NL</span>
        <span className={`${COL.started} ${CELL_BORDER} ${HEADER_STYLE}`}>
          Started
        </span>
        <span className={`${COL.duration} ${CELL_BORDER} ${HEADER_STYLE}`}>
          Duration
        </span>
        <span className={`${COL.score} ${CELL_BORDER} ${HEADER_STYLE}`}>
          Score
        </span>
        <span className={`${COL.evals} px-3 text-right ${HEADER_STYLE}`}>
          Evals
        </span>
        <span className="w-3 shrink-0" />
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        {trajectories.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-[13px] text-envoi-text-dim">
            No trajectories
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
                  (
                  {[...modelMap.values()].reduce(
                    (sum, traces) => sum + traces.length,
                    0,
                  )}{" "}
                  trajectories)
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
                    const finalPassed = trace.finalPassed;
                    const totalTests = Math.max(
                      trace.totalTests > 0
                        ? trace.totalTests
                        : trace.suites
                          ? computeTotalTests(trace.suites)
                          : TOTAL_TESTS,
                      finalPassed,
                    );
                    const pct =
                      totalTests > 0 ? (finalPassed / totalTests) * 100 : 0;
                    const evalCount = trace.evalCount ?? 0;
                    const live = liveIds.has(trace.id);

                    return (
                      <Link
                        key={trace.id}
                        href={`/project/${encodeURIComponent(project)}/trajectory/${trace.id}`}
                        className="flex items-center border-b border-envoi-border-light px-3.5 py-2.5 transition-colors hover:bg-envoi-surface"
                      >
                        {/* ID + live badge */}
                        <span
                          className={`${COL.id} ${CELL_BORDER} flex items-center gap-1.5 pl-0`}
                        >
                          {live && (
                            <span className="relative flex h-1.75 w-1.75 shrink-0">
                              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
                              <span className="relative inline-flex h-1.75 w-1.75 rounded-full bg-emerald-500" />
                            </span>
                          )}
                          <span className="truncate text-[13px] font-medium text-envoi-text">
                            {trace.id}
                          </span>
                        </span>

                        {/* Target */}
                        <span
                          className={`${COL.target} ${CELL_BORDER} truncate text-[12px] text-envoi-text-dim`}
                        >
                          {(trace.params.target ?? "").split("-")[0]}
                        </span>

                        {/* Impl Language */}
                        <span
                          className={`${COL.lang} ${CELL_BORDER} truncate text-[12px] text-envoi-text-dim`}
                        >
                          {trace.params.implLang ?? ""}
                        </span>

                        {/* Natural Language */}
                        <span
                          className={`${COL.nl} ${CELL_BORDER} truncate text-[12px] text-envoi-text-dim`}
                        >
                          {trace.params.lang ?? ""}
                        </span>

                        {/* Date started */}
                        <span
                          className={`${COL.started} ${CELL_BORDER} whitespace-nowrap font-mono text-[12px] text-envoi-text-muted`}
                        >
                          {formatDate(trace.startedAt, showYear)}
                        </span>

                        {/* Duration */}
                        <span
                          className={`${COL.duration} ${CELL_BORDER} whitespace-nowrap text-[12px] text-envoi-text-muted`}
                        >
                          {trace.duration || "—"}
                        </span>

                        {/* Progress bar + score */}
                        <div
                          className={`${COL.score} ${CELL_BORDER} flex items-center gap-2`}
                        >
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
                        <span
                          className={`${COL.evals} whitespace-nowrap px-3 text-right text-[12px] text-envoi-text-dim`}
                        >
                          {evalCount > 0 ? `${evalCount} evals` : "—"}
                        </span>

                        {/* Arrow */}
                        <ArrowUpRight
                          size={12}
                          className="w-3 shrink-0 text-envoi-text-dim"
                        />
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
