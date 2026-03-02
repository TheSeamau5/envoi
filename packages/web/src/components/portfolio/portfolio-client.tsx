/**
 * Portfolio dashboard client component.
 *
 * Two views:
 * 1. Model Rankings — per-model pass rate bars across environments (original table)
 * 2. Environment Summary — per-environment rows with best score, median, cost, and run count
 *
 * Includes:
 * - Sorting controls (by name, best score, run count, cost)
 * - Pareto scatter plot integration
 * - Model rankings table
 */

"use client";

import { useState, useMemo } from "react";
import type { PortfolioRow, PortfolioEnvironmentRow, ParetoPoint } from "@/lib/types";
import { T } from "@/lib/tokens";
import { ParetoScatter } from "./pareto-scatter";

type PortfolioClientProps = {
  rows: PortfolioRow[];
  environments: string[];
  environmentRows: PortfolioEnvironmentRow[];
  paretoPoints: ParetoPoint[];
};

type ViewTab = "environments" | "rankings" | "pareto";

type EnvSortKey = "name" | "bestScore" | "runCount" | "cost";
type EnvSortDir = "asc" | "desc";

/** Rank badge colors: gold, silver, bronze, default */
function rankColor(rank: number): string {
  switch (rank) {
    case 1:
      return T.gold;
    case 2:
      return T.textDim;
    case 3:
      return T.accent;
    default:
      return T.textDim;
  }
}

/** Format token count as compact label */
function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(0)}K`;
  }
  return String(tokens);
}

/** Portfolio dashboard with environment summaries, model rankings, and Pareto scatter */
export function PortfolioClient({ rows, environments, environmentRows, paretoPoints }: PortfolioClientProps) {
  const [activeTab, setActiveTab] = useState<ViewTab>("environments");
  const [envSortKey, setEnvSortKey] = useState<EnvSortKey>("name");
  const [envSortDir, setEnvSortDir] = useState<EnvSortDir>("asc");

  /** Toggle sort direction or change sort key */
  function handleEnvSort(key: EnvSortKey) {
    if (envSortKey === key) {
      setEnvSortDir(envSortDir === "asc" ? "desc" : "asc");
    }
    else {
      setEnvSortKey(key);
      setEnvSortDir(key === "name" ? "asc" : "desc");
    }
  }

  /** Sorted environment rows */
  const sortedEnvRows = useMemo(() => {
    const sorted = [...environmentRows];
    sorted.sort((rowA, rowB) => {
      let compare = 0;
      switch (envSortKey) {
        case "name":
          compare = rowA.environment.localeCompare(rowB.environment);
          break;
        case "bestScore":
          compare = (rowA.bestTotal > 0 ? rowA.bestPassed / rowA.bestTotal : 0) - (rowB.bestTotal > 0 ? rowB.bestPassed / rowB.bestTotal : 0);
          break;
        case "runCount":
          compare = rowA.runCount - rowB.runCount;
          break;
        case "cost":
          compare = rowA.totalTokens - rowB.totalTokens;
          break;
      }
      return envSortDir === "asc" ? compare : -compare;
    });
    return sorted;
  }, [environmentRows, envSortKey, envSortDir]);

  /** Unique environments for Pareto scatter */
  const paretoEnvironments = useMemo(() => {
    const envSet = new Set<string>();
    for (const point of paretoPoints) {
      envSet.add(point.environment);
    }
    return Array.from(envSet).sort();
  }, [paretoPoints]);

  /** Sort direction indicator */
  function sortArrow(key: EnvSortKey): string {
    if (envSortKey !== key) {
      return "";
    }
    return envSortDir === "asc" ? " \u2191" : " \u2193";
  }

  const hasData = rows.length > 0 || environmentRows.length > 0;

  if (!hasData) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-envoi-text-dim">
        No portfolio data available
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto">
      {/* View tabs */}
      <div className="flex items-center gap-[2px] border-b border-envoi-border bg-envoi-bg px-4 py-[6px]">
        <button
          onClick={() => setActiveTab("environments")}
          className={`rounded-md px-[10px] py-[4px] text-[12px] font-semibold transition-colors ${
            activeTab === "environments"
              ? "bg-envoi-text text-white"
              : "text-envoi-text-dim hover:bg-envoi-surface"
          }`}
        >
          Environments
        </button>
        <button
          onClick={() => setActiveTab("rankings")}
          className={`rounded-md px-[10px] py-[4px] text-[12px] font-semibold transition-colors ${
            activeTab === "rankings"
              ? "bg-envoi-text text-white"
              : "text-envoi-text-dim hover:bg-envoi-surface"
          }`}
        >
          Model Rankings
        </button>
        <button
          onClick={() => setActiveTab("pareto")}
          className={`rounded-md px-[10px] py-[4px] text-[12px] font-semibold transition-colors ${
            activeTab === "pareto"
              ? "bg-envoi-text text-white"
              : "text-envoi-text-dim hover:bg-envoi-surface"
          }`}
        >
          Pareto Frontier
        </button>
      </div>

      <div className="p-4">
        {/* Environment summary view */}
        {activeTab === "environments" && (
          <div className="overflow-auto">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr>
                  <th
                    className="sticky top-0 cursor-pointer border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted hover:text-envoi-text"
                    onClick={() => handleEnvSort("name")}
                  >
                    Environment{sortArrow("name")}
                  </th>
                  <th
                    className="sticky top-0 cursor-pointer border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-right font-semibold text-envoi-text-muted hover:text-envoi-text"
                    onClick={() => handleEnvSort("bestScore")}
                  >
                    Best Score{sortArrow("bestScore")}
                  </th>
                  <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted">
                    Best Model
                  </th>
                  <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-right font-semibold text-envoi-text-muted">
                    Median Pass Rate
                  </th>
                  <th
                    className="sticky top-0 cursor-pointer border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-right font-semibold text-envoi-text-muted hover:text-envoi-text"
                    onClick={() => handleEnvSort("runCount")}
                  >
                    Runs{sortArrow("runCount")}
                  </th>
                  <th
                    className="sticky top-0 cursor-pointer border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-right font-semibold text-envoi-text-muted hover:text-envoi-text"
                    onClick={() => handleEnvSort("cost")}
                  >
                    Total Tokens{sortArrow("cost")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedEnvRows.map((envRow, rowIndex) => {
                  const bestRate = envRow.bestTotal > 0 ? envRow.bestPassed / envRow.bestTotal : 0;
                  return (
                    <tr
                      key={envRow.environment}
                      className={rowIndex % 2 === 0 ? "bg-envoi-bg" : "bg-envoi-surface"}
                    >
                      <td className="border-b border-envoi-border-light px-[14px] py-[8px] font-semibold text-envoi-text">
                        {envRow.environment}
                      </td>
                      <td className="border-b border-envoi-border-light px-[14px] py-[8px] text-right">
                        <div className="flex items-center justify-end gap-[8px]">
                          <div
                            className="h-[6px] rounded-full"
                            style={{
                              width: `${Math.max(4, bestRate * 100)}%`,
                              maxWidth: 80,
                              background: T.greenDark,
                            }}
                          />
                          <span className="text-envoi-text-muted">
                            {envRow.bestPassed}/{envRow.bestTotal} ({(bestRate * 100).toFixed(0)}%)
                          </span>
                        </div>
                      </td>
                      <td className="border-b border-envoi-border-light px-[14px] py-[8px] text-envoi-text-muted">
                        {envRow.bestModel}
                      </td>
                      <td className="border-b border-envoi-border-light px-[14px] py-[8px] text-right text-envoi-text-muted">
                        {(envRow.medianPassRate * 100).toFixed(1)}%
                      </td>
                      <td className="border-b border-envoi-border-light px-[14px] py-[8px] text-right text-envoi-text-muted">
                        {envRow.runCount}
                      </td>
                      <td className="border-b border-envoi-border-light px-[14px] py-[8px] text-right text-envoi-text-muted">
                        {formatTokens(envRow.totalTokens)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {sortedEnvRows.length === 0 && (
              <div className="flex items-center justify-center py-[40px] text-[13px] text-envoi-text-dim">
                No environment data available
              </div>
            )}
          </div>
        )}

        {/* Model rankings view (original table) */}
        {activeTab === "rankings" && (
          <div className="overflow-auto">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr>
                  <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted">
                    Rank
                  </th>
                  <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted">
                    Model
                  </th>
                  <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-right font-semibold text-envoi-text-muted">
                    Avg Rank
                  </th>
                  {environments.map((env) => (
                    <th
                      key={env}
                      className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted"
                    >
                      {env}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIndex) => (
                  <tr
                    key={row.model}
                    className={rowIndex % 2 === 0 ? "bg-envoi-bg" : "bg-envoi-surface"}
                  >
                    <td className="border-b border-envoi-border-light px-[14px] py-[6px]">
                      <span
                        className="font-semibold"
                        style={{ color: rankColor(rowIndex + 1) }}
                      >
                        #{rowIndex + 1}
                      </span>
                    </td>
                    <td className="border-b border-envoi-border-light px-[14px] py-[6px] font-semibold text-envoi-text">
                      {row.model}
                    </td>
                    <td className="border-b border-envoi-border-light px-[14px] py-[6px] text-right text-envoi-text-muted">
                      {row.avgRank.toFixed(1)}
                    </td>
                    {environments.map((env) => {
                      const envData = row.environments[env];
                      const passRate = envData?.passRate ?? 0;
                      return (
                        <td
                          key={env}
                          className="border-b border-envoi-border-light px-[14px] py-[6px]"
                        >
                          <div className="flex items-center gap-[8px]">
                            <div
                              className="h-[6px] rounded-full"
                              style={{
                                width: `${Math.max(2, passRate * 100)}%`,
                                maxWidth: 100,
                                background: T.greenDark,
                              }}
                            />
                            <span className="text-[13px] text-envoi-text-muted">
                              {(passRate * 100).toFixed(0)}%
                            </span>
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>

            {rows.length === 0 && (
              <div className="flex items-center justify-center py-[40px] text-[13px] text-envoi-text-dim">
                No model ranking data available
              </div>
            )}
          </div>
        )}

        {/* Pareto frontier scatter plot view */}
        {activeTab === "pareto" && (
          <div>
            {paretoPoints.length > 0 ? (
              <ParetoScatter points={paretoPoints} environments={paretoEnvironments} />
            ) : (
              <div className="flex items-center justify-center py-[40px] text-[13px] text-envoi-text-dim">
                No Pareto data available
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
