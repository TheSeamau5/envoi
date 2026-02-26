/**
 * Steps panel with feedback banner and step list.
 * Client component — renders steps with stagger animation on commit change.
 *
 * Step type icons and colors:
 * - REASONING: Diamond, #f97316
 * - READ: ArrowRight, #2563eb
 * - WRITE: ArrowLeft, #059669
 * - TOOL: Terminal, #a17a08
 * - TEST: Play, #059669
 * - MCP: Hexagon, #c026a3
 */

"use client";

import { useState, useEffect, useRef } from "react";
import {
  Diamond,
  ArrowRight,
  ArrowLeft,
  Terminal,
  Play,
  Hexagon,
  CheckCircle2,
  AlertTriangle,
  Minus,
} from "lucide-react";
import type { Commit, Step } from "@/lib/types";
import { T } from "@/lib/tokens";

type StepsPanelProps = {
  commit: Commit;
};

/** Map step type to icon and color */
const STEP_CONFIG: Record<
  Step["type"],
  { icon: typeof Diamond; color: string; label: string }
> = {
  reasoning: { icon: Diamond, color: "#f97316", label: "REASONING" },
  file_read: { icon: ArrowRight, color: "#2563eb", label: "READ" },
  file_write: { icon: ArrowLeft, color: "#059669", label: "WRITE" },
  tool_call: { icon: Terminal, color: "#a17a08", label: "TOOL" },
  test_run: { icon: Play, color: "#059669", label: "TEST" },
  mcp_call: { icon: Hexagon, color: "#c026a3", label: "MCP" },
};

/** Individual step row */
function StepRow({
  step,
  stepIndex,
  isFirstReasoning,
}: {
  step: Step;
  stepIndex: number;
  isFirstReasoning: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const config = STEP_CONFIG[step.type];
  const StepIcon = config.icon;

  const hasExpandableDetail =
    isFirstReasoning && step.detail.length > step.summary.length;

  return (
    <div
      className="border-b border-envoi-border-light px-[14px] py-[10px]"
      style={{
        animation: `stepFadeIn 0.3s ease both`,
        animationDelay: `${stepIndex * 40}ms`,
      }}
    >
      <div className="flex items-start gap-[10px]">
        {/* Icon in colored square */}
        <div
          className="flex shrink-0 items-center justify-center rounded"
          style={{
            width: 26,
            height: 26,
            background: `${config.color}12`,
          }}
        >
          <StepIcon size={13} style={{ color: config.color }} />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          {/* Type label + step counter */}
          <div className="flex items-center gap-[6px]">
            <span
              className="font-semibold tracking-[0.06em]"
              style={{ fontSize: 9, color: config.color }}
            >
              {config.label}
            </span>
            <span className="text-[9px] text-envoi-text-dim">
              #{step.index + 1}
            </span>
          </div>

          {/* Summary */}
          <div className="mt-[2px] text-[11px] leading-[16px] text-envoi-text">
            {step.summary}
          </div>

          {/* Expandable detail for first reasoning step */}
          {hasExpandableDetail && (
            <div className="mt-[6px]">
              <button
                onClick={() => setExpanded((prev) => !prev)}
                className="text-[9px] font-medium text-envoi-accent hover:underline"
              >
                {expanded ? "Show less" : "Show more"}
              </button>
              {expanded && (
                <div className="relative mt-[4px]">
                  <div className="text-[10px] leading-[15px] text-envoi-text-muted">
                    {step.detail}
                  </div>
                </div>
              )}
              {!expanded && (
                <div className="relative mt-[4px] max-h-[36px] overflow-hidden">
                  <div className="text-[10px] leading-[15px] text-envoi-text-muted">
                    {step.detail}
                  </div>
                  {/* CSS mask fade */}
                  <div
                    className="pointer-events-none absolute inset-0"
                    style={{
                      background:
                        "linear-gradient(to bottom, transparent 0%, white 90%)",
                    }}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Feedback banner at the top of the steps panel */
function FeedbackBanner({ commit }: { commit: Commit }) {
  const { feedback } = commit;
  const hasRegression = feedback.newlyBroken > 0;
  const hasProgress = feedback.passedDelta > 0;
  const isNeutral = !hasRegression && !hasProgress;

  const bgColor = hasRegression ? T.redBg : hasProgress ? T.greenBg : T.surface;
  const textColor = hasRegression
    ? T.redDark
    : hasProgress
      ? T.greenDark
      : T.textMuted;
  const BannerIcon = hasRegression
    ? AlertTriangle
    : hasProgress
      ? CheckCircle2
      : Minus;

  return (
    <div
      className="sticky top-0 z-10 flex items-center gap-[8px] border-b border-envoi-border px-[14px] py-[8px]"
      style={{ background: bgColor }}
    >
      <BannerIcon size={13} style={{ color: textColor }} />
      <div className="flex items-center gap-[10px]">
        {hasProgress && (
          <span className="text-[10px] font-semibold" style={{ color: T.greenDark }}>
            +{feedback.newlyFixed} fixed
          </span>
        )}
        {hasRegression && (
          <span className="text-[10px] font-semibold" style={{ color: T.redDark }}>
            {feedback.newlyBroken} broken
          </span>
        )}
        {isNeutral && (
          <span className="text-[10px] font-medium" style={{ color: T.textMuted }}>
            No change
          </span>
        )}
      </div>
      <div className="flex-1" />
      <span className="text-[9px]" style={{ color: textColor }}>
        {feedback.totalPassed} passed / {feedback.totalFailed} failed
      </span>
    </div>
  );
}

export function StepsPanel({ commit }: StepsPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const prevCommitIndex = useRef(commit.index);

  /** Reset scroll to top when commit changes */
  useEffect(() => {
    if (prevCommitIndex.current !== commit.index && containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
    prevCommitIndex.current = commit.index;
  }, [commit.index]);

  /** Track if we've seen the first reasoning step */
  let firstReasoningSeen = false;

  return (
    <div ref={containerRef} className="flex flex-1 flex-col overflow-y-auto">
      {/* Inline keyframes for stagger animation */}
      <style>{`
        @keyframes stepFadeIn {
          from {
            opacity: 0;
            transform: translateY(6px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>

      <FeedbackBanner commit={commit} />

      {/* Step list */}
      <div key={commit.index}>
        {commit.steps.map((step, stepIndex) => {
          const isFirstReasoning =
            step.type === "reasoning" && !firstReasoningSeen;
          if (step.type === "reasoning" && !firstReasoningSeen) {
            firstReasoningSeen = true;
          }
          return (
            <StepRow
              key={`${commit.index}-${step.index}`}
              step={step}
              stepIndex={stepIndex}
              isFirstReasoning={isFirstReasoning}
            />
          );
        })}
      </div>
    </div>
  );
}
