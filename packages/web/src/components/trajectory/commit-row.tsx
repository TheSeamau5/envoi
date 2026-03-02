/**
 * Single commit row in the timeline commit list.
 * Client component — handles click and scroll-into-view.
 */

"use client";

import { useEffect, useRef } from "react";
import type { Commit, Suite } from "@/lib/types";
import { SUITES as DEFAULT_SUITES } from "@/lib/constants";
import { SUITE_COLORS, T } from "@/lib/tokens";
import { Star, TrendingDown } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type CommitRowProps = {
  commit: Commit;
  isSelected: boolean;
  onSelect: (index: number) => void;
  activeSuite: string;
  suites?: Suite[];
};

export function CommitRow({ commit, isSelected, onSelect, activeSuite, suites: suitesProp }: CommitRowProps) {
  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isSelected && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [isSelected]);

  const leftBorderColor = isSelected
    ? T.accent
    : commit.isRegression
      ? T.red
      : commit.isMilestone
        ? T.gold
        : "transparent";

  const bgColor = isSelected ? T.accentBg : "transparent";

  const suitesToShow =
    activeSuite === "all"
      ? effectiveSuites
      : effectiveSuites.filter((suite) => suite.name === activeSuite);

  return (
    <div
      ref={rowRef}
      onClick={() => onSelect(commit.index)}
      className="flex cursor-pointer items-center gap-3 border-b border-envoi-border-light px-[14px] py-[10px] transition-colors hover:bg-envoi-surface"
      style={{
        borderLeft: `3px solid ${leftBorderColor}`,
        background: bgColor,
      }}
    >
      {/* Hash + turn */}
      <div className="flex min-w-[90px] flex-col gap-[2px]">
        <span className="text-[13px] font-semibold text-envoi-text">
          {commit.hash.slice(0, 8)}
        </span>
        <span className="text-[13px] text-envoi-text-dim">
          turn {commit.turn}
        </span>
      </div>

      {/* Delta + total */}
      <div className="flex min-w-[60px] flex-col items-end gap-[2px]">
        <span
          className="text-[13px] font-semibold"
          style={{
            color:
              commit.delta > 0
                ? T.greenDark
                : commit.delta < 0
                  ? T.redDark
                  : T.textMuted,
          }}
        >
          {commit.delta > 0 ? `+${commit.delta}` : commit.delta === 0 ? "±0" : `${commit.delta}`}
        </span>
        <span className="text-[13px] text-envoi-text-dim">
          {commit.totalPassed} total
        </span>
      </div>

      {/* Mini suite bars */}
      <div className="flex flex-1 items-center gap-[6px]">
          {suitesToShow.map((suite) => {
            const passed = commit.suiteState[suite.name] ?? 0;
            const ratio = passed / suite.total;
            const suiteColor = SUITE_COLORS[suite.name];
            return (
              <Tooltip key={suite.name}>
                <TooltipTrigger asChild>
                  <div className="flex-1">
                    <div
                      className="h-[4px] rounded-full"
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

      {/* Feedback badges */}
      <div className="flex items-center gap-[6px]">
        {commit.feedback.newlyBroken > 0 && (
          <span
            className="flex items-center gap-[3px] rounded-[3px] px-[5px] py-[1px] text-[13px] font-medium"
            style={{ color: T.redDark, background: T.redBg }}
          >
            <TrendingDown size={9} />
            {commit.feedback.newlyBroken}
          </span>
        )}
        {commit.isMilestone && (
          <span
            className="flex items-center gap-[3px] rounded-[3px] px-[5px] py-[1px] text-[13px] font-medium"
            style={{ color: T.gold, background: T.goldBg }}
          >
            <Star size={9} />
          </span>
        )}
      </div>

    </div>
  );
}
