/**
 * Single commit row in the timeline commit list.
 * Client component — handles click and scroll-into-view.
 *
 * Shows: hash, turn, test delta (+passed / -broken), LOC delta (+added / -removed),
 * mini suite bars, milestone/regression badges, and criticality indicators.
 *
 * Criticality heuristics (all computed from existing commit data):
 * - Large score delta: |delta| > 5 tests changed
 * - Suite transition: first time a suite goes from 0 to >0 passed
 * - Regression recovery: positive delta following one or more negative deltas
 */

"use client";

import { useEffect, useRef } from "react";
import type { Commit, Suite } from "@/lib/types";
import { SUITES as DEFAULT_SUITES } from "@/lib/constants";
import { SUITE_COLORS, T } from "@/lib/tokens";
import { Star, Diamond } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** Criticality classification for a commit */
export type CriticalityTag = "large_delta" | "suite_transition" | "regression_recovery";

/** Minimum absolute delta to be considered a large score change */
const LARGE_DELTA_THRESHOLD = 5;

/**
 * Compute criticality tags for a commit based on its position in the trajectory.
 * Returns an array of tags (empty if the commit is not critical).
 */
export function computeCriticality(
  commit: Commit,
  commitIndex: number,
  allCommits: Commit[],
  suites: Suite[],
): CriticalityTag[] {
  const tags: CriticalityTag[] = [];

  if (Math.abs(commit.delta) > LARGE_DELTA_THRESHOLD) {
    tags.push("large_delta");
  }

  if (commitIndex > 0) {
    const prevCommit = allCommits[commitIndex - 1];
    if (prevCommit) {
      for (const suite of suites) {
        const prevPassed = prevCommit.suiteState[suite.name] ?? 0;
        const currentPassed = commit.suiteState[suite.name] ?? 0;
        if (prevPassed === 0 && currentPassed > 0) {
          tags.push("suite_transition");
          break;
        }
      }
    }
  }

  if (commitIndex > 0 && commit.delta > 0) {
    let foundRegression = false;
    for (let lookback = commitIndex - 1; lookback >= 0; lookback--) {
      const prev = allCommits[lookback];
      if (!prev) {
        break;
      }
      if (prev.delta < 0) {
        foundRegression = true;
        break;
      }
      if (prev.delta > 0) {
        break;
      }
    }
    if (foundRegression) {
      tags.push("regression_recovery");
    }
  }

  return tags;
}

/** Format criticality tags into a human-readable tooltip string */
function criticalityLabel(tags: CriticalityTag[]): string {
  const labels: string[] = [];
  for (const tag of tags) {
    switch (tag) {
      case "large_delta":
        labels.push("Large score change");
        break;
      case "suite_transition":
        labels.push("New suite unlocked");
        break;
      case "regression_recovery":
        labels.push("Recovery from regression");
        break;
    }
  }
  return labels.join(", ");
}

/** Format a duration in minutes into a compact label */
function formatElapsed(minutes: number): string {
  if (minutes < 1) {
    return `${Math.round(minutes * 60)}s`;
  }
  if (minutes < 60) {
    return `${Math.round(minutes)}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remaining = Math.round(minutes % 60);
  if (remaining === 0) {
    return `${hours}h`;
  }
  return `${hours}h${remaining}m`;
}

type CommitRowProps = {
  commit: Commit;
  isSelected: boolean;
  isFocused: boolean;
  onSelect: (index: number) => void;
  activeSuite: string;
  suites?: Suite[];
  /** Pre-computed criticality tags for this commit */
  criticalityTags?: CriticalityTag[];
  /** Minutes elapsed since previous commit/turn (undefined for first row) */
  elapsedSincePrev?: number;
  /** Previous commit — used to compute suite-scoped deltas */
  prevCommit?: Commit;
};

/** Single commit row with optional criticality indicator */
export function CommitRow({ commit, isSelected, isFocused, onSelect, activeSuite, suites: suitesProp, criticalityTags, elapsedSincePrev, prevCommit }: CommitRowProps) {
  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isSelected && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [isSelected]);

  const isCritical = criticalityTags !== undefined && criticalityTags.length > 0;

  const leftBorderColor = isSelected
    ? (isFocused ? T.accent : T.borderLight)
    : commit.isRegression
      ? T.red
      : commit.isMilestone
        ? T.gold
        : isCritical
          ? T.accent
          : "transparent";

  const bgColor = isSelected
    ? (isFocused ? T.accentBg : T.surface)
    : "transparent";

  const suitesToShow =
    activeSuite === "all"
      ? effectiveSuites
      : effectiveSuites.filter((suite) => suite.name === activeSuite);

  /** Suite-scoped passed count and total */
  const scopedPassed = activeSuite === "all"
    ? commit.totalPassed
    : commit.suiteState[activeSuite] ?? 0;
  const activeSuiteDef = effectiveSuites.find((suite) => suite.name === activeSuite);
  const scopedTotal = activeSuite === "all"
    ? effectiveSuites.reduce((sum, suite) => sum + suite.total, 0)
    : activeSuiteDef?.total ?? 0;

  /** Suite-scoped delta */
  const prevPassed = prevCommit
    ? (activeSuite === "all" ? prevCommit.totalPassed : prevCommit.suiteState[activeSuite] ?? 0)
    : 0;
  const scopedDelta = prevCommit ? scopedPassed - prevPassed : scopedPassed;
  const scopedFixed = Math.max(0, scopedDelta);
  const scopedBroken = Math.max(0, -scopedDelta);

  return (
    <div
      ref={rowRef}
      onClick={() => onSelect(commit.index)}
      className="flex cursor-pointer items-center gap-3 border-b border-envoi-border-light px-3.5 py-2.5 transition-colors hover:bg-envoi-surface"
      style={{
        borderLeft: `3px solid ${leftBorderColor}`,
        background: bgColor,
      }}
    >
      {/* Hash + turn + elapsed */}
      <div className="flex min-w-22.5 flex-col gap-0.5">
        <span className="text-[13px] font-semibold text-envoi-text">
          {commit.hash.slice(0, 8)}
        </span>
        <span className="text-[13px] text-envoi-text-dim">
          turn {commit.turn}
          {elapsedSincePrev !== undefined && (
            <span className="ml-1.5 text-envoi-text-muted">
              +{formatElapsed(elapsedSincePrev)}
            </span>
          )}
        </span>
      </div>

      {/* Test delta: +passed / -broken (suite-scoped) */}
      <div className="flex min-w-17.5 flex-col items-end gap-0.5">
        <div className="flex items-center gap-1.5">
          {scopedFixed > 0 && (
            <span className="text-[13px] font-semibold" style={{ color: T.greenDark }}>
              +{scopedFixed}
            </span>
          )}
          {scopedBroken > 0 && (
            <span className="text-[13px] font-semibold" style={{ color: T.redDark }}>
              -{scopedBroken}
            </span>
          )}
          {scopedFixed === 0 && scopedBroken === 0 && (
            <span className="text-[13px] font-semibold text-envoi-text-muted">
              ±0
            </span>
          )}
        </div>
        <span className="text-[13px] text-envoi-text-dim">
          {scopedPassed} / {scopedTotal}
        </span>
      </div>

      {/* Mini suite bars */}
      <div className="flex flex-1 items-center gap-1.5">
          {suitesToShow.map((suite) => {
            const passed = commit.suiteState[suite.name] ?? 0;
            const ratio = passed / suite.total;
            const suiteColor = SUITE_COLORS[suite.name];
            return (
              <Tooltip key={suite.name}>
                <TooltipTrigger asChild>
                  <div className="flex-1">
                    <div
                      className="h-1 rounded-full"
                      style={{ background: suiteColor?.bg ?? T.borderLight }}
                    >
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${(ratio * 100).toFixed(1)}%`,
                          background: suiteColor?.color ?? T.textDim,
                        }}
                      />
                    </div>
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  {suite.name}: {passed}/{suite.total}
                </TooltipContent>
              </Tooltip>
            );
          })}
      </div>

      {/* Milestone + criticality badges */}
      <div className="flex items-center gap-1.5">
        {isCritical && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="flex items-center gap-0.75 rounded-[3px] px-1.25 py-px text-[13px] font-medium"
                style={{ color: T.accent, background: T.accentBg }}
              >
                <Diamond size={9} />
              </span>
            </TooltipTrigger>
            <TooltipContent>{criticalityLabel(criticalityTags)}</TooltipContent>
          </Tooltip>
        )}
        {commit.isMilestone && (
          <span
            className="flex items-center gap-0.75 rounded-[3px] px-1.25 py-px text-[13px] font-medium"
            style={{ color: T.gold, background: T.goldBg }}
          >
            <Star size={9} />
          </span>
        )}
      </div>
    </div>
  );
}
