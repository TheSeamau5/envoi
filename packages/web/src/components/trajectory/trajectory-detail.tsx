/**
 * Main client component for the Trajectory Detail page.
 * Resizable split layout: left panel (Timeline / Tests & Metrics) and right panel (Steps / Code).
 *
 * Manages all interactive state: selected commit, playback, tabs, suite filter,
 * panel visibility, divider position. Keyboard navigation for commit selection.
 */

"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import type { Trajectory, DetailLeftTab, DetailRightTab } from "@/lib/types";
import { SUITES } from "@/lib/constants";
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

  /** Selected commit index */
  const [selectedIndex, setSelectedIndex] = useState(0);

  /** Playback state */
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  /** Tab state */
  const [leftTab, setLeftTab] = useState<DetailLeftTab>("timeline");
  const [rightTab, setRightTab] = useState<DetailRightTab>("steps");

  /** Suite filter for timeline */
  const [activeSuite, setActiveSuite] = useState("all");

  /**
   * Right panel open/close + divider position.
   * Initial values come from server-read cookies so SSR matches hydration.
   * On change we write back to cookies for the next SSR pass.
   */
  const [rightPanelOpen, setRightPanelOpenRaw] = useState(initialRightPanelOpen);
  const [dividerPct, setDividerPctRaw] = useState(initialDividerPct);

  const setRightPanelOpen = useCallback((open: boolean) => {
    setRightPanelOpenRaw(open);
    setLayoutCookie("rightPanelOpen", open);
  }, []);

  const setDividerPct = useCallback((pct: number) => {
    setDividerPctRaw(pct);
    setLayoutCookie("dividerPct", pct);
  }, []);

  /** Refs for drag + keyboard */
  const containerRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);

  const selectedCommit = commits[selectedIndex];

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
          (event.target as HTMLElement).blur();
          break;
      }
    },
    [selectedIndex, commits.length, handleSelectCommit, handleTogglePlay],
  );

  /** Auto-focus on mount for keyboard */
  useEffect(() => {
    containerRef.current?.focus();
  }, []);

  /** Draggable divider handlers */
  const handleDividerMouseDown = useCallback(() => {
    isDragging.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const handleMouseMove = (event: MouseEvent) => {
      if (!isDragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const percentage = ((event.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.max(25, Math.min(75, percentage));
      setDividerPct(Math.round(clamped));
    };

    const handleMouseUp = () => {
      isDragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  }, [setDividerPct]);

  if (!selectedCommit) return undefined;

  return (
    <div
      ref={containerRef}
      className="flex flex-1 overflow-hidden outline-none"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      {/* Left panel */}
      <div
        className="flex flex-col overflow-hidden"
        style={{ width: rightPanelOpen ? `${dividerPct}%` : "100%" }}
      >
        {/* Tab bar */}
        <div className="flex h-[41px] shrink-0 items-center border-b border-envoi-border">
          <TabButton
            label="Timeline"
            isActive={leftTab === "timeline"}
            onClick={() => setLeftTab("timeline")}
          />
          <TabButton
            label="Tests & Metrics"
            isActive={leftTab === "tests"}
            onClick={() => setLeftTab("tests")}
          />
          {!rightPanelOpen && (
            <>
              <div className="flex-1" />
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={() => setRightPanelOpen(true)}
                    className="mr-2 flex h-[24px] w-[24px] items-center justify-center rounded text-envoi-text-dim hover:bg-envoi-surface hover:text-envoi-text"
                  >
                    <PanelRightOpen size={14} />
                  </button>
                </TooltipTrigger>
                <TooltipContent>Open right panel</TooltipContent>
              </Tooltip>
            </>
          )}
        </div>

        {/* Left tab content */}
        {leftTab === "timeline" ? (
          <div className="flex flex-1 flex-col overflow-hidden">
            {/* Progress curve */}
            <ProgressCurve
              commits={commits}
              selectedIndex={selectedIndex}
              onSelect={handleSelectCommit}
              activeSuite={activeSuite}
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
            <div className="flex items-center gap-[4px] border-b border-envoi-border px-[14px] py-[6px]">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={() => setActiveSuite("all")}
                    className={`rounded-full px-[8px] py-[2px] text-[9px] font-semibold transition-colors ${
                      activeSuite === "all"
                        ? "bg-envoi-text text-white"
                        : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
                    }`}
                  >
                    all
                  </button>
                </TooltipTrigger>
                <TooltipContent>All suites: {SUITES.reduce((sum, suite) => sum + suite.total, 0)} tests</TooltipContent>
              </Tooltip>
              {SUITES.map((suite) => (
                <Tooltip key={suite.name}>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setActiveSuite(suite.name)}
                      className={`rounded-full px-[8px] py-[2px] text-[9px] font-semibold transition-colors ${
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
                />
              ))}
            </div>
          </div>
        ) : (
          <TestsPanel commit={selectedCommit} />
        )}
      </div>

      {/* Draggable divider */}
      {rightPanelOpen && (
        <div
          onMouseDown={handleDividerMouseDown}
          className="flex w-[5px] shrink-0 cursor-col-resize items-center justify-center border-x border-envoi-border-light hover:bg-envoi-accent-bg active:bg-envoi-accent-bg"
          style={{ touchAction: "none" }}
        >
          <div className="h-[24px] w-[2px] rounded-full bg-envoi-border" />
        </div>
      )}

      {/* Right panel */}
      {rightPanelOpen && (
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Tab bar */}
          <div className="flex h-[41px] shrink-0 items-center border-b border-envoi-border">
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
            <div className="flex-1" />
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={() => setRightPanelOpen(false)}
                  className="mr-2 flex h-[24px] w-[24px] items-center justify-center rounded text-envoi-text-dim hover:bg-envoi-surface hover:text-envoi-text"
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
          ) : (
            <CodePanel commit={selectedCommit} />
          )}
        </div>
      )}
    </div>
  );
}

/** Tab button for panel tab bars */
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
      className={`px-[14px] py-[8px] text-[10px] font-semibold uppercase tracking-[0.06em] transition-colors ${
        isActive
          ? "border-b-[2px] border-envoi-accent text-envoi-accent"
          : "border-b-[2px] border-transparent text-envoi-text-dim hover:text-envoi-text"
      }`}
    >
      {label}
    </button>
  );
}
