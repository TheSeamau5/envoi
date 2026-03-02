/**
 * Steps panel with feedback banner and expandable step list.
 * Client component — renders steps with stagger animation on commit change.
 *
 * All steps are expandable. Expanded content varies by type:
 * - reasoning: THINKING section + optional PLAN section
 * - tool_call / mcp_call: INPUT (JSON) + OUTPUT (text, red if error)
 * - file_read / file_write: INPUT (path JSON) + OUTPUT (code/confirmation)
 * - test_run: RESULTS section with pass/fail summary
 *
 * Step type icons and colors (from T.step* tokens):
 * - REASONING: Diamond
 * - READ: ArrowRight
 * - WRITE: ArrowLeft
 * - TOOL: Terminal
 * - TEST: Play
 * - MCP: Hexagon
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
  ChevronRight,
  ChevronDown,
  AlertCircle,
  MessageSquare,
  GitBranch,
  Type,
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
  reasoning: { icon: Diamond, color: T.stepReasoning, label: "REASONING" },
  file_read: { icon: ArrowRight, color: T.stepRead, label: "READ" },
  file_write: { icon: ArrowLeft, color: T.stepWrite, label: "WRITE" },
  tool_call: { icon: Terminal, color: T.stepTool, label: "TOOL" },
  test_run: { icon: Play, color: T.stepTest, label: "TEST" },
  mcp_call: { icon: Hexagon, color: T.stepMcp, label: "MCP" },
  text: { icon: Type, color: T.stepText, label: "TEXT" },
  spawn: { icon: GitBranch, color: T.stepSpawn, label: "SPAWN" },
  message: { icon: MessageSquare, color: T.stepMessage, label: "MESSAGE" },
};

/** Format milliseconds into a human-friendly duration */
function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(1)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  if (minutes < 60) {
    return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

/** Section label used in expanded content */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[13px] font-semibold uppercase tracking-[0.06em] text-envoi-text-dim">
      {children}
    </span>
  );
}

/** Monospace content box for tool input/output */
function MonoBox({
  children,
  isError,
  maxHeight,
}: {
  children: React.ReactNode;
  isError?: boolean;
  maxHeight?: number;
}) {
  return (
    <div
      className="overflow-auto rounded-[4px] border px-[10px] py-[8px] text-[12px] leading-[18px]"
      style={{
        maxHeight: maxHeight ?? 240,
        fontFamily: T.mono,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        background: isError ? T.redBg : T.surface,
        borderColor: isError ? T.redBorderLight : T.borderLight,
        color: isError ? T.redDark : T.text,
      }}
    >
      {children}
    </div>
  );
}

/** Expanded content for reasoning steps */
function ReasoningExpanded({ step }: { step: Step }) {
  return (
    <div className="mt-[8px] flex flex-col gap-[8px]">
      {step.reasoningContent && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Thinking</SectionLabel>
          <MonoBox maxHeight={300}>{step.reasoningContent}</MonoBox>
        </div>
      )}
      {step.planContent && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Plan</SectionLabel>
          <MonoBox maxHeight={200}>{step.planContent}</MonoBox>
        </div>
      )}
      {!step.reasoningContent && !step.planContent && step.detail && (
        <div className="text-[12px] leading-[18px] text-envoi-text-muted">
          {step.detail}
        </div>
      )}
    </div>
  );
}

/** Expanded content for tool_call / mcp_call steps */
function ToolExpanded({ step }: { step: Step }) {
  return (
    <div className="mt-[8px] flex flex-col gap-[8px]">
      {step.toolInput && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Input</SectionLabel>
          <MonoBox>{step.toolInput}</MonoBox>
        </div>
      )}
      {step.toolOutput && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Output</SectionLabel>
          <MonoBox isError={step.isError}>{step.toolOutput}</MonoBox>
        </div>
      )}
      {step.isError && step.errorMessage && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Error</SectionLabel>
          <MonoBox isError>{step.errorMessage}</MonoBox>
        </div>
      )}
    </div>
  );
}

/** Expanded content for file_read / file_write steps */
function FileExpanded({ step }: { step: Step }) {
  return (
    <div className="mt-[8px] flex flex-col gap-[8px]">
      {step.toolInput && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Input</SectionLabel>
          <MonoBox>{step.toolInput}</MonoBox>
        </div>
      )}
      {step.toolOutput && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Output</SectionLabel>
          <MonoBox>{step.toolOutput}</MonoBox>
        </div>
      )}
    </div>
  );
}

/** Expanded content for test_run steps */
function TestExpanded({ step }: { step: Step }) {
  return (
    <div className="mt-[8px] flex flex-col gap-[8px]">
      {step.toolInput && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Command</SectionLabel>
          <MonoBox>{step.toolInput}</MonoBox>
        </div>
      )}
      {step.toolOutput && (
        <div className="flex flex-col gap-[4px]">
          <SectionLabel>Results</SectionLabel>
          <MonoBox isError={step.isError}>{step.toolOutput}</MonoBox>
        </div>
      )}
    </div>
  );
}

/** Expanded content for text / spawn / message steps */
function TextExpanded({ step }: { step: Step }) {
  return (
    <div className="mt-[8px] flex flex-col gap-[8px]">
      <div className="flex flex-col gap-[4px]">
        <SectionLabel>Full Text</SectionLabel>
        <MonoBox maxHeight={400}>{step.detail}</MonoBox>
      </div>
    </div>
  );
}

/** Pick the right expanded content for a step type */
function ExpandedContent({ step }: { step: Step }) {
  switch (step.type) {
    case "reasoning":
      return <ReasoningExpanded step={step} />;
    case "tool_call":
    case "mcp_call":
      return <ToolExpanded step={step} />;
    case "file_read":
    case "file_write":
      return <FileExpanded step={step} />;
    case "test_run":
      return <TestExpanded step={step} />;
    case "text":
    case "spawn":
    case "message":
      return <TextExpanded step={step} />;
    default:
      return undefined;
  }
}

/** Check whether a step has expandable content */
function hasExpandableContent(step: Step): boolean {
  return !!(
    step.toolInput ||
    step.toolOutput ||
    step.reasoningContent ||
    step.planContent ||
    step.errorMessage ||
    (step.detail && step.detail.length > step.summary.length)
  );
}

/** Individual step row */
function StepRow({
  step,
  stepIndex,
}: {
  step: Step;
  stepIndex: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const config = STEP_CONFIG[step.type];
  const StepIcon = config.icon;
  const expandable = hasExpandableContent(step);

  const ChevronIcon = expanded ? ChevronDown : ChevronRight;

  return (
    <div
      className="border-b border-envoi-border-light px-[14px] py-[10px]"
      style={{
        animation: `stepFadeIn 0.3s ease both`,
        animationDelay: `${stepIndex * 40}ms`,
      }}
    >
      {/* Clickable header */}
      <div
        className={`flex items-start gap-[10px] ${expandable ? "cursor-pointer" : ""}`}
        onClick={expandable ? () => setExpanded((prev) => !prev) : undefined}
      >
        {/* Expand chevron */}
        <div className="flex w-[14px] shrink-0 items-center pt-[6px]">
          {expandable ? (
            <ChevronIcon size={12} className="text-envoi-text-dim" />
          ) : (
            <div className="w-[12px]" />
          )}
        </div>

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
          {/* Type label + step counter + error dot */}
          <div className="flex items-center gap-[6px]">
            <span
              className="font-semibold tracking-[0.06em]"
              style={{ fontSize: 13, color: config.color }}
            >
              {config.label}
            </span>
            <span className="text-[13px] text-envoi-text-dim">
              #{step.index + 1}
            </span>
            {step.isError && (
              <AlertCircle size={10} style={{ color: T.redDark }} />
            )}
          </div>

          {/* Summary */}
          <div className="mt-[2px] break-all text-[13px] leading-[18px] text-envoi-text">
            {step.summary || (step.type === "reasoning" ? (
              <span className="italic text-envoi-text-muted">Reasoning not shown</span>
            ) : step.summary)}
          </div>

          {/* Metadata line */}
          {(step.durationMs !== undefined || step.tokensUsed !== undefined) && (
            <div className="mt-[2px] flex items-center gap-[8px] text-[13px] text-envoi-text-dim">
              {step.durationMs !== undefined && (
                <span>{formatDuration(step.durationMs)}</span>
              )}
              {step.tokensUsed !== undefined && (
                <span>{step.tokensUsed.toLocaleString()} tokens</span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="ml-[50px]">
          <ExpandedContent step={step} />
        </div>
      )}
    </div>
  );
}

/** Feedback banner at the top of the steps panel */
function FeedbackBanner({ commit }: { commit: Commit }) {
  const { feedback } = commit;
  const hasRegression = feedback.newlyBroken > 0;
  const hasProgress = feedback.passedDelta > 0;
  const isNeutral = !hasRegression && !hasProgress;

  /** Opaque backgrounds for the sticky banner — prevents text bleed-through from scrolling content */
  const bgColor = hasRegression
    ? T.redBgOpaque
    : hasProgress
      ? T.greenBgOpaque
      : T.surface;
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
          <span className="text-[12px] font-semibold" style={{ color: T.greenDark }}>
            +{feedback.newlyFixed} fixed
          </span>
        )}
        {hasRegression && (
          <span className="text-[12px] font-semibold" style={{ color: T.redDark }}>
            {feedback.newlyBroken} broken
          </span>
        )}
        {isNeutral && (
          <span className="text-[12px] font-medium" style={{ color: T.textMuted }}>
            No change
          </span>
        )}
      </div>
      <div className="flex-1" />
      <span className="text-[13px]" style={{ color: textColor }}>
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

  return (
    <div ref={containerRef} className="flex flex-1 flex-col overflow-y-auto overflow-x-hidden">
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

      {/* Step list — filter out steps with no displayable content */}
      <div key={commit.index}>
        {commit.steps
          .filter((step) => step.summary.length > 0 || step.detail.length > 0)
          .map((step, stepIndex) => (
            <StepRow
              key={`${commit.index}-${step.index}`}
              step={step}
              stepIndex={stepIndex}
            />
          ))}
      </div>
    </div>
  );
}
