"use client";

import { useMemo, useState } from "react";
import type { Commit, ResolvedTrajectoryLog } from "@/lib/types";
import { T } from "@/lib/tokens";
import { formatDate } from "@/lib/utils";

type LogsPanelProps = {
  logs: ResolvedTrajectoryLog[];
  commit: Commit;
  selectedCommitPosition: number;
  groupByTurn: boolean;
  isLoading?: boolean;
  isLive?: boolean;
};

export function LogsPanel({
  logs,
  commit,
  selectedCommitPosition,
  groupByTurn,
  isLoading = false,
  isLive = false,
}: LogsPanelProps) {
  const scopedLogs = useMemo(() => {
    if (groupByTurn) {
      return logs.filter((log) => log.resolvedTurn === commit.turn);
    }
    return logs.filter(
      (log) => log.resolvedCommitIndex === selectedCommitPosition,
    );
  }, [logs, selectedCommitPosition, groupByTurn, commit.turn]);

  const prefixes = useMemo(() => {
    const values = new Set<string>();
    for (const log of scopedLogs) {
      values.add(log.prefix);
    }
    return ["all", ...[...values].sort()];
  }, [scopedLogs]);

  const [activePrefix, setActivePrefix] = useState("all");

  const effectivePrefix = prefixes.includes(activePrefix)
    ? activePrefix
    : "all";
  const filtered = useMemo(() => {
    if (effectivePrefix === "all") {
      return scopedLogs;
    }
    return scopedLogs.filter((log) => log.prefix === effectivePrefix);
  }, [scopedLogs, effectivePrefix]);

  if (isLoading && logs.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-envoi-text-dim">
        Loading logs...
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="border-b border-envoi-border px-3.5 py-2.5">
        <div className="flex items-center gap-2 text-[12px] text-envoi-text-dim">
          {isLive && (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.08em] text-emerald-700">
              live
            </span>
          )}
        </div>
        <div className="mt-1.5 text-[12px] text-envoi-text-dim">
          {groupByTurn
            ? `${filtered.length} / ${scopedLogs.length} rows in turn ${commit.turn}`
            : `${filtered.length} / ${scopedLogs.length} rows in commit ${commit.hash.slice(0, 10)}`}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {prefixes.map((prefix) => (
            <button
              key={prefix}
              onClick={() => setActivePrefix(prefix)}
              className={`rounded-full px-2 py-0.5 text-[12px] font-semibold transition-colors ${
                effectivePrefix === prefix
                  ? "bg-envoi-accent text-white"
                  : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
              }`}
            >
              {prefix}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="px-3.5 py-3 text-[13px] text-envoi-text-dim">
            No logs for this selection.
          </div>
        ) : (
          filtered.map((log) => (
            <div
              key={log.seq}
              className="border-b border-envoi-border-light px-3.5 py-2.5"
            >
              <div className="flex items-center gap-2 text-[12px] text-envoi-text-dim">
                <span className="font-semibold text-envoi-text">
                  #{log.seq}
                </span>
                <span>{formatDate(log.ts)}</span>
                {log.resolvedTurn !== undefined && (
                  <span>turn {log.resolvedTurn}</span>
                )}
                {log.resolvedPart !== undefined && (
                  <span>part {log.resolvedPart}</span>
                )}
                {log.resolvedCommitHash && (
                  <span
                    className="rounded-xs px-1.25 py-px"
                    style={{ background: T.surface }}
                  >
                    {log.resolvedCommitHash.slice(0, 10)}
                  </span>
                )}
                <span
                  className="rounded-xs px-1.25 py-px uppercase"
                  style={{ background: T.surface }}
                >
                  {log.prefix}
                </span>
              </div>
              <pre
                className="mt-1.5 rounded-sm border px-2.5 py-2 text-[12px] leading-4.5"
                style={{
                  fontFamily: T.mono,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  background: T.surface,
                  borderColor: T.borderLight,
                  color: T.text,
                }}
              >
                {log.message}
              </pre>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
