/**
 * Steps panel with feedback banner and expandable step list.
 * Display component — keyboard navigation is handled by the parent.
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

import { useState, useEffect, useRef, useCallback, useMemo, forwardRef, useImperativeHandle } from "react";
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
import { StepsTimeline } from "./steps-timeline";

type StepsPanelProps = {
  commit: Commit;
  selectedStepIndex?: number;
  isFocused: boolean;
  onSelectStep: (index: number) => void;
};

/** Imperative handle for parent keyboard navigation */
export type StepsPanelHandle = {
  toggleExpand: (index: number) => void;
  scrollToStep: (index: number) => void;
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
      className="overflow-auto rounded-sm border px-2.5 py-2 text-[12px] leading-4.5"
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
    <div className="mt-2 flex flex-col gap-2">
      {step.reasoningContent && (
        <div className="flex flex-col gap-1">
          <SectionLabel>Thinking</SectionLabel>
          <MonoBox maxHeight={300}>{step.reasoningContent}</MonoBox>
        </div>
      )}
      {step.planContent && (
        <div className="flex flex-col gap-1">
          <SectionLabel>Plan</SectionLabel>
          <MonoBox maxHeight={200}>{step.planContent}</MonoBox>
        </div>
      )}
      {!step.reasoningContent && !step.planContent && step.detail && (
        <div className="text-[12px] leading-4.5 text-envoi-text-muted">
          {step.detail}
        </div>
      )}
    </div>
  );
}

/** Expanded content for tool_call / mcp_call steps */
function ToolExpanded({ step }: { step: Step }) {
  return (
    <div className="mt-2 flex flex-col gap-2">
      {step.toolInput && (
        <div className="flex flex-col gap-1">
          <SectionLabel>Input</SectionLabel>
          <MonoBox>{step.toolInput}</MonoBox>
        </div>
      )}
      {step.toolOutput && (
        <div className="flex flex-col gap-1">
          <SectionLabel>Output</SectionLabel>
          <MonoBox isError={step.isError}>{step.toolOutput}</MonoBox>
        </div>
      )}
      {step.isError && step.errorMessage && (
        <div className="flex flex-col gap-1">
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
    <div className="mt-2 flex flex-col gap-2">
      {step.toolInput && (
        <div className="flex flex-col gap-1">
          <SectionLabel>Input</SectionLabel>
          <MonoBox>{step.toolInput}</MonoBox>
        </div>
      )}
      {step.toolOutput && (
        <div className="flex flex-col gap-1">
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
    <div className="mt-2 flex flex-col gap-2">
      {step.toolInput && (
        <div className="flex flex-col gap-1">
          <SectionLabel>Command</SectionLabel>
          <MonoBox>{step.toolInput}</MonoBox>
        </div>
      )}
      {step.toolOutput && (
        <div className="flex flex-col gap-1">
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
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex flex-col gap-1">
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

/** Individual step row — expand state controlled by parent */
function StepRow({
  step,
  stepIndex,
  isSelected,
  isFocused,
  expanded,
  onSelect,
  rowRef,
}: {
  step: Step;
  stepIndex: number;
  isSelected: boolean;
  isFocused: boolean;
  expanded: boolean;
  onSelect: (index: number) => void;
  rowRef: (element: HTMLDivElement | null) => void;
}) {
  const config = STEP_CONFIG[step.type];
  const StepIcon = config.icon;
  const expandable = hasExpandableContent(step);

  const ChevronIcon = expanded ? ChevronDown : ChevronRight;

  return (
    <div
      ref={rowRef}
      className="border-b border-envoi-border-light px-3.5 py-2.5"
      style={{
        background: isSelected ? (isFocused ? T.accentBg : T.surface) : undefined,
        borderLeft: isSelected ? `3px solid ${isFocused ? T.accent : T.borderLight}` : "3px solid transparent",
      }}
    >
      {/* Clickable header */}
      <div
        className={`flex items-start gap-2.5 ${expandable ? "cursor-pointer" : ""}`}
        onClick={() => onSelect(stepIndex)}
      >
        {/* Expand chevron */}
        <div className="flex w-3.5 shrink-0 items-center pt-1.5">
          {expandable ? (
            <ChevronIcon size={12} className="text-envoi-text-dim" />
          ) : (
            <div className="w-3" />
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
          <div className="flex items-center gap-1.5">
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
          <div className="mt-0.5 break-all text-[13px] leading-4.5 text-envoi-text">
            {step.summary || (step.type === "reasoning" ? (
              <span className="italic text-envoi-text-muted">Reasoning not shown</span>
            ) : step.summary)}
          </div>

          {/* Metadata line */}
          {(step.durationMs !== undefined || step.tokensUsed !== undefined) && (
            <div className="mt-0.5 flex items-center gap-2 text-[13px] text-envoi-text-dim">
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
        <div className="ml-12.5">
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
      className="sticky top-0 z-10 flex items-center gap-2 border-b border-envoi-border px-3.5 py-2"
      style={{ background: bgColor }}
    >
      <BannerIcon size={13} style={{ color: textColor }} />
      <div className="flex items-center gap-2.5">
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

/** Steps panel with fixed timeline and scrollable step list */
export const StepsPanel = forwardRef<StepsPanelHandle, StepsPanelProps>(
  function StepsPanel({ commit, selectedStepIndex, isFocused, onSelectStep }, ref) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevCommitIndex = useRef(commit.index);
  const stepRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());

  /** Expose toggleExpand and scrollToStep to parent */
  useImperativeHandle(ref, () => ({
    toggleExpand: (index: number) => {
      setExpandedSteps((prev) => {
        const next = new Set(prev);
        if (next.has(index)) {
          next.delete(index);
        } else {
          next.add(index);
        }
        return next;
      });
    },
    scrollToStep: (index: number) => {
      const element = stepRefs.current.get(index);
      if (element) {
        element.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    },
  }), []);

  /** Filter steps once for both timeline and list */
  const filteredSteps = useMemo(
    () => commit.steps.filter((step) => step.summary.length > 0 || step.detail.length > 0),
    [commit.steps],
  );

  /** Reset scroll and expand state when commit changes */
  useEffect(() => {
    if (prevCommitIndex.current !== commit.index) {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = 0;
      }
      setExpandedSteps(new Set());
      stepRefs.current.clear();
    }
    prevCommitIndex.current = commit.index;
  }, [commit.index]);

  /** Handle step click — select + toggle expand */
  const handleStepClick = useCallback((index: number) => {
    onSelectStep(index);
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, [onSelectStep]);

  /** Ref callback factory for step rows */
  const getStepRef = useCallback((index: number) => {
    return (element: HTMLDivElement | null) => {
      if (element) {
        stepRefs.current.set(index, element);
      } else {
        stepRefs.current.delete(index);
      }
    };
  }, []);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Fixed: feedback banner + timeline */}
      <FeedbackBanner commit={commit} />
      <StepsTimeline
        steps={filteredSteps}
        selectedStepIndex={selectedStepIndex}
        onSelectStep={onSelectStep}
      />

      {/* Scrollable step list */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto overflow-x-hidden outline-none"
      >
        <div key={commit.index}>
          {filteredSteps.map((step, stepIndex) => (
            <StepRow
              key={`${commit.index}-${stepIndex}`}
              step={step}
              stepIndex={stepIndex}
              isSelected={selectedStepIndex === stepIndex}
              isFocused={isFocused}
              expanded={expandedSteps.has(stepIndex)}
              onSelect={handleStepClick}
              rowRef={getStepRef(stepIndex)}
            />
          ))}
        </div>
      </div>
    </div>
  );
});
