/**
 * Main client component for the Trajectory Detail page.
 * Resizable split layout: left panel (Timeline / Tests & Metrics) and right panel (Steps / Code).
 *
 * Manages all interactive state: selected commit, playback, tabs, suite filter,
 * panel visibility, divider position. Keyboard navigation for commit selection.
 *
 * Panel open/close is animated with react-spring. Both panels are always mounted;
 * the right panel's width animates to 0 when closed.
 */

"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useSpring, animated } from "@react-spring/web";
import type { Trajectory, DetailRightTab, Suite, CodeSnapshot, Commit } from "@/lib/types";
import { SUITES as DEFAULT_SUITES, computeTotalTests } from "@/lib/constants";
import { T } from "@/lib/tokens";
import { setLayoutCookie } from "@/lib/cookies.client";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ProgressCurve } from "./progress-curve";
import { PlayControls } from "./play-controls";
import { CommitRow } from "./commit-row";
import { TestsPanel } from "./tests-panel";
import { StepsPanel } from "./steps-panel";
import { CodePanel } from "./code-panel";
import { WastePanel } from "./waste-panel";

type TrajectoryDetailProps = {
  trajectory: Trajectory;
  /** Server-read initial values — eliminates FOUC on panel layout */
  initialRightPanelOpen: boolean;
  initialDividerPct: number;
};

export function TrajectoryDetail({
  trajectory,
  initialRightPanelOpen,
  initialDividerPct,
}: TrajectoryDetailProps) {
  const { commits } = trajectory;
  const suites: Suite[] = trajectory.suites ?? DEFAULT_SUITES;
  const totalTests = computeTotalTests(suites);

  /** Selected commit index */
  const [selectedIndex, setSelectedIndex] = useState(0);

  /** Playback state */
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  /** Tab state */
  const [rightTab, setRightTab] = useState<DetailRightTab>("steps");

  /** Suite filter for timeline */
  const [activeSuite, setActiveSuite] = useState("all");

  /** Code history — fetched lazily from code_snapshots.parquet */
  const [codeHistory, setCodeHistory] = useState<Record<number, CodeSnapshot>>();

  useEffect(() => {
    let cancelled = false;
    async function fetchCodeHistory() {
      try {
        const response = await fetch(`/api/trajectories/${encodeURIComponent(trajectory.id)}/code-history`);
        if (!response.ok) {
          return;
        }
        const data: Record<string, CodeSnapshot> = await response.json();
        if (!cancelled) {
          /** Convert string keys from JSON to numeric keys */
          const mapped: Record<number, CodeSnapshot> = {};
          for (const [key, snapshot] of Object.entries(data)) {
            mapped[Number(key)] = snapshot;
          }
          setCodeHistory(mapped);
        }
      } catch {
        /** Code history is optional — silently ignore fetch errors */
      }
    }
    fetchCodeHistory();
    return () => {
      cancelled = true;
    };
  }, [trajectory.id]);

  /**
   * Right panel open/close + divider position.
   * Initial values come from server-read cookies so SSR matches hydration.
   * On change we write back to cookies for the next SSR pass.
   */
  const [rightPanelOpen, setRightPanelOpenRaw] = useState(initialRightPanelOpen);
  const [dividerPct, setDividerPctRaw] = useState(initialDividerPct);
  const [dragging, setDragging] = useState(false);

  const setRightPanelOpen = useCallback((open: boolean) => {
    setRightPanelOpenRaw(open);
    setLayoutCookie("rightPanelOpen", open);
  }, []);

  /** Animated panel layout — immediate during drag for responsiveness */
  const panelSpring = useSpring({
    leftWidth: rightPanelOpen ? dividerPct : 100,
    rightWidth: rightPanelOpen ? 100 - dividerPct : 0,
    dividerWidth: rightPanelOpen ? 9 : 0,
    rightOpacity: rightPanelOpen ? 1 : 0,
    config: { tension: 300, friction: 30 },
    immediate: (key: string) => dragging && (key === "leftWidth" || key === "rightWidth"),
  });

  /** Refs for drag + keyboard */
  const containerRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);

  const selectedCommit = commits[selectedIndex];

  /** Merge code snapshot into selected commit when code history is available */
  const enrichedCommit: Commit | undefined = useMemo(() => {
    if (!selectedCommit) {
      return undefined;
    }
    const snapshot = codeHistory?.[selectedCommit.index];
    if (!snapshot) {
      return selectedCommit;
    }
    return {
      ...selectedCommit,
      codeSnapshot: snapshot,
    };
  }, [selectedCommit, codeHistory]);

  const handleSelectCommit = useCallback(
    (index: number) => {
      const clamped = Math.max(0, Math.min(commits.length - 1, index));
      setSelectedIndex(clamped);
    },
    [commits.length],
  );

  const handleTogglePlay = useCallback(() => {
    setIsPlaying((prev) => !prev);
  }, []);

  const handleSpeedChange = useCallback((newSpeed: number) => {
    setSpeed(newSpeed);
  }, []);

  /** Keyboard navigation */
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      switch (event.key) {
        case "ArrowUp":
        case "k":
          event.preventDefault();
          handleSelectCommit(selectedIndex - 1);
          break;
        case "ArrowDown":
        case "j":
          event.preventDefault();
          handleSelectCommit(selectedIndex + 1);
          break;
        case "ArrowLeft":
          event.preventDefault();
          handleSelectCommit(selectedIndex - 1);
          break;
        case "ArrowRight":
          event.preventDefault();
          handleSelectCommit(selectedIndex + 1);
          break;
        case " ":
          event.preventDefault();
          handleTogglePlay();
          break;
        case "Home":
          event.preventDefault();
          handleSelectCommit(0);
          break;
        case "End":
          event.preventDefault();
          handleSelectCommit(commits.length - 1);
          break;
        case "Escape":
          if (event.target instanceof HTMLElement) {
            event.target.blur();
          }
          break;
      }
    },
    [selectedIndex, commits.length, handleSelectCommit, handleTogglePlay],
  );

  /** Auto-focus on mount for keyboard */
  useEffect(() => {
    containerRef.current?.focus();
  }, []);

  /** Draggable divider handlers — uses raw state during drag (no cookie, no spring) for responsiveness */
  const handleDividerMouseDown = useCallback(() => {
    isDragging.current = true;
    setDragging(true);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    let lastPct = 0;

    const handleMouseMove = (event: MouseEvent) => {
      if (!isDragging.current || !containerRef.current) {
        return;
      }
      const rect = containerRef.current.getBoundingClientRect();
      const percentage = ((event.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.round(Math.max(25, Math.min(75, percentage)));
      lastPct = clamped;
      setDividerPctRaw(clamped);
    };

    const handleMouseUp = () => {
      isDragging.current = false;
      setDragging(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (lastPct > 0) {
        setLayoutCookie("dividerPct", lastPct);
      }
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  }, []);

  if (!selectedCommit) {
    return undefined;
  }

  return (
    <div
      ref={containerRef}
      className="flex flex-1 overflow-hidden outline-none"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      {/* Left panel — always shows timeline */}
      <animated.div
        className="flex flex-col overflow-hidden"
        style={{ width: panelSpring.leftWidth.to((width) => `${width}%`) }}
      >
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Progress curve */}
          <ProgressCurve
            commits={commits}
            selectedIndex={selectedIndex}
            onSelect={handleSelectCommit}
            activeSuite={activeSuite}
            suites={suites}
            totalTests={totalTests}
          />

          {/* Playback controls */}
          <PlayControls
            totalCommits={commits.length}
            selectedIndex={selectedIndex}
            onSelect={handleSelectCommit}
            isPlaying={isPlaying}
            onTogglePlay={handleTogglePlay}
            speed={speed}
            onSpeedChange={handleSpeedChange}
          />

          {/* Suite filter pills */}
          <div className="flex flex-nowrap items-center gap-[4px] overflow-x-auto border-b border-envoi-border px-[14px] py-[6px]">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={() => setActiveSuite("all")}
                  className={`shrink-0 rounded-full px-[8px] py-[2px] text-[13px] font-semibold transition-colors ${
                    activeSuite === "all"
                      ? "bg-envoi-text text-white"
                      : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
                  }`}
                >
                  all
                </button>
              </TooltipTrigger>
              <TooltipContent>All suites: {totalTests} tests</TooltipContent>
            </Tooltip>
            {suites.map((suite) => (
              <Tooltip key={suite.name}>
                <TooltipTrigger asChild>
                  <button
                    onClick={() => setActiveSuite(suite.name)}
                    className={`shrink-0 rounded-full px-[8px] py-[2px] text-[13px] font-semibold transition-colors ${
                      activeSuite === suite.name
                        ? "bg-envoi-text text-white"
                        : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
                    }`}
                  >
                    {suite.name}
                  </button>
                </TooltipTrigger>
                <TooltipContent>{suite.name}: {suite.total} tests</TooltipContent>
              </Tooltip>
            ))}
            {!rightPanelOpen && (
              <>
                <div className="flex-1" />
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setRightPanelOpen(true)}
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-envoi-text-dim hover:bg-envoi-surface hover:text-envoi-text"
                    >
                      <PanelRightOpen size={14} />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>Open right panel</TooltipContent>
                </Tooltip>
              </>
            )}
          </div>

          {/* Commit list (scrollable) */}
          <div className="flex-1 overflow-y-auto">
            {commits.map((commit) => (
              <CommitRow
                key={commit.index}
                commit={commit}
                isSelected={commit.index === selectedIndex}
                onSelect={handleSelectCommit}
                activeSuite={activeSuite}
                suites={suites}
              />
            ))}
          </div>
        </div>
      </animated.div>

      {/* Draggable divider — always mounted, width animates to 0 */}
      <animated.div
        onMouseDown={rightPanelOpen ? handleDividerMouseDown : undefined}
        className="group relative flex shrink-0 items-center justify-center"
        style={{
          width: panelSpring.dividerWidth.to((width) => `${width}px`),
          cursor: rightPanelOpen ? "col-resize" : "default",
          touchAction: "none",
          overflow: "hidden",
        }}
      >
        {/* Thin vertical line */}
        <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-envoi-border-light" />
        {/* Capsule handle — turns orange on hover/drag */}
        <div
          className={`relative z-10 h-[36px] w-[4px] rounded-full transition-colors ${
            dragging
              ? ""
              : "bg-envoi-border group-hover:bg-envoi-accent"
          }`}
          style={dragging ? { background: T.accent } : undefined}
        />
      </animated.div>

      {/* Right panel — always mounted, width + opacity animated */}
      <animated.div
        className="flex flex-col overflow-hidden"
        style={{
          width: panelSpring.rightWidth.to((width) => `${width}%`),
          opacity: panelSpring.rightOpacity,
        }}
      >
        {/* Tab bar */}
        <div className="flex h-[41px] shrink-0 items-stretch border-b border-envoi-border">
          <TabButton
            label="Steps"
            isActive={rightTab === "steps"}
            onClick={() => setRightTab("steps")}
          />
          <TabButton
            label="Code"
            isActive={rightTab === "code"}
            onClick={() => setRightTab("code")}
          />
          <TabButton
            label="Tests & Metrics"
            isActive={rightTab === "tests"}
            onClick={() => setRightTab("tests")}
          />
          <TabButton
            label="Waste"
            isActive={rightTab === "waste"}
            onClick={() => setRightTab("waste")}
          />
          <div className="flex-1" />
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => setRightPanelOpen(false)}
                className="mr-2 flex h-6 w-6 shrink-0 self-center items-center justify-center rounded text-envoi-text-dim hover:bg-envoi-surface hover:text-envoi-text"
              >
                <PanelRightClose size={14} />
              </button>
            </TooltipTrigger>
            <TooltipContent>Close right panel</TooltipContent>
          </Tooltip>
        </div>

        {/* Right tab content */}
        {rightTab === "steps" ? (
          <StepsPanel commit={selectedCommit} />
        ) : rightTab === "code" ? (
          <CodePanel commit={enrichedCommit ?? selectedCommit} />
        ) : rightTab === "waste" ? (
          <WastePanel trajectoryId={trajectory.id} />
        ) : (
          <TestsPanel commit={selectedCommit} suites={suites} totalTests={totalTests} />
        )}
      </animated.div>
    </div>
  );
}

/**
 * Tab button for panel tab bars.
 * Uses items-stretch on the parent so the border-b aligns flush with the
 * container's bottom border (orange active indicator overlaps gray border).
 */
function TabButton({
  label,
  isActive,
  onClick,
}: {
  label: string;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center px-[14px] text-[12px] font-semibold uppercase tracking-[0.06em] transition-colors ${
        isActive
          ? "border-b-[2px] border-envoi-accent text-envoi-accent"
          : "border-b-[2px] border-transparent text-envoi-text-dim hover:text-envoi-text"
      }`}
    >
      {label}
    </button>
  );
}
