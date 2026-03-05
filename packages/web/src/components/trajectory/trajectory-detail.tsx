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

import { useState, useCallback, useRef, useMemo } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useSpring, animated } from "@react-spring/web";
import type { Trajectory, Suite, CodeSnapshot, Commit, Step, ChangedFile } from "@/lib/types";
import { SUITES as DEFAULT_SUITES, computeTotalTests } from "@/lib/constants";
import { usePersistedState } from "@/lib/storage";
import { T } from "@/lib/tokens";
import { setLayoutCookie } from "@/lib/cookies.client";
import { useLiveTrajectory } from "@/lib/use-live-trajectory";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ProgressCurve } from "./progress-curve";
import { PlayControls } from "./play-controls";
import { CommitRow, computeCriticality } from "./commit-row";
import type { CriticalityTag } from "./commit-row";
import { TestsPanel } from "./tests-panel";
import { StepsPanel } from "./steps-panel";
import { CodePanel } from "./code-panel";

type TrajectoryDetailProps = {
  trajectory: Trajectory;
  project: string;
  /** Server-read initial values — eliminates FOUC on panel layout */
  initialRightPanelOpen: boolean;
  initialDividerPct: number;
};

/** Right panel tab options */
type RightTab = "steps" | "code" | "tests";

/** Main trajectory detail view with resizable split layout */
export function TrajectoryDetail({
  trajectory: initialTrajectory,
  project,
  initialRightPanelOpen,
  initialDividerPct,
}: TrajectoryDetailProps) {
  const { trajectory, isLive } = useLiveTrajectory(initialTrajectory, project);
  const { commits } = trajectory;
  const suites: Suite[] = trajectory.suites ?? DEFAULT_SUITES;
  const totalTests = computeTotalTests(suites);

  /** Selected commit index */
  const [selectedIndex, setSelectedIndex] = useState(0);

  /** Playback state */
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  /** Tab state */
  const [rightTab, setRightTab] = useState<RightTab>("steps");

  /** Suite filter for timeline */
  const [activeSuite, setActiveSuite] = useState("all");

  /** Critical-only filter */
  const [showCriticalOnly, setShowCriticalOnly] = useState(false);

  /** Turn-centric view toggle — persisted globally */
  const [groupByTurn, setGroupByTurn] = usePersistedState("groupByTurn", false);

  /** Group commits by turn: pick last commit per turn, aggregate steps + changedFiles */
  const effectiveCommits: Commit[] = useMemo(() => {
    if (!groupByTurn) {
      return commits;
    }
    const turnMap = new Map<number, Commit[]>();
    for (const commit of commits) {
      const group = turnMap.get(commit.turn);
      if (group) {
        group.push(commit);
      } else {
        turnMap.set(commit.turn, [commit]);
      }
    }
    const grouped: Commit[] = [];
    for (const [, turnCommits] of turnMap) {
      const lastCommit = turnCommits[turnCommits.length - 1];
      if (!lastCommit) {
        continue;
      }
      const allSteps: Step[] = [];
      const allChangedFiles: ChangedFile[] = [];
      for (const commit of turnCommits) {
        allSteps.push(...commit.steps);
        allChangedFiles.push(...commit.changedFiles);
      }
      grouped.push({ ...lastCommit, steps: allSteps, changedFiles: allChangedFiles });
    }
    /** Recompute deltas relative to previous turn */
    for (let idx = 1; idx < grouped.length; idx++) {
      const current = grouped[idx];
      const prev = grouped[idx - 1];
      if (!current || !prev) {
        continue;
      }
      const turnDelta = current.totalPassed - prev.totalPassed;
      grouped[idx] = {
        ...current,
        delta: turnDelta,
        isRegression: turnDelta < 0,
        feedback: {
          ...current.feedback,
          passedDelta: turnDelta,
        },
      };
    }
    return grouped;
  }, [commits, groupByTurn]);

  /** Code history — fetched lazily from code_snapshots.parquet.
   *  Ref-guarded fetch in render body — no useEffect needed. */
  const [codeHistory, setCodeHistory] =
    useState<Record<number, CodeSnapshot>>();
  const codeHistoryFetchRef = useRef<string | null>(null);

  if (codeHistoryFetchRef.current !== trajectory.id) {
    codeHistoryFetchRef.current = trajectory.id;
    const fetchId = trajectory.id;
    fetch(
      `/api/trajectories/${encodeURIComponent(fetchId)}/code-history?project=${encodeURIComponent(project)}`,
    )
      .then((response) => {
        if (!response.ok) {
          return null;
        }
        return response.json();
      })
      .then((data: Record<string, CodeSnapshot> | null) => {
        if (!data || codeHistoryFetchRef.current !== fetchId) {
          return;
        }
        /** Convert string keys from JSON to numeric keys */
        const mapped: Record<number, CodeSnapshot> = {};
        for (const [key, snapshot] of Object.entries(data)) {
          mapped[Number(key)] = snapshot;
        }
        setCodeHistory(mapped);
      })
      .catch(() => {
        /** Code history is optional — silently ignore fetch errors */
      });
  }

  /**
   * Right panel open/close + divider position.
   * Initial values come from server-read cookies so SSR matches hydration.
   * On change we write back to cookies for the next SSR pass.
   */
  const [rightPanelOpen, setRightPanelOpenRaw] = useState(
    initialRightPanelOpen,
  );
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
    immediate: (key: string) =>
      dragging && (key === "leftWidth" || key === "rightWidth"),
  });

  /** Refs for drag + keyboard */
  const containerRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);

  /** Clamp index when switching between commits/turns mode */
  const safeIndex = Math.min(selectedIndex, effectiveCommits.length - 1);
  const selectedCommit = effectiveCommits[safeIndex];

  /** Pre-compute criticality tags for all effective commits */
  const criticalityMap: Map<number, CriticalityTag[]> = useMemo(() => {
    const map = new Map<number, CriticalityTag[]>();
    for (let commitIdx = 0; commitIdx < effectiveCommits.length; commitIdx++) {
      const commit = effectiveCommits[commitIdx];
      if (!commit) {
        continue;
      }
      const tags = computeCriticality(commit, commitIdx, effectiveCommits, suites);
      if (tags.length > 0) {
        map.set(commitIdx, tags);
      }
    }
    return map;
  }, [effectiveCommits, suites]);

  /** Filter commits when showCriticalOnly is enabled */
  const visibleCommits = useMemo(() => {
    if (!showCriticalOnly) {
      return effectiveCommits;
    }
    return effectiveCommits.filter((_, commitIdx) => criticalityMap.has(commitIdx));
  }, [effectiveCommits, showCriticalOnly, criticalityMap]);

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
      const clamped = Math.max(0, Math.min(effectiveCommits.length - 1, index));
      setSelectedIndex(clamped);
    },
    [effectiveCommits.length],
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
          handleSelectCommit(safeIndex - 1);
          break;
        case "ArrowDown":
        case "j":
          event.preventDefault();
          handleSelectCommit(safeIndex + 1);
          break;
        case "ArrowLeft":
          event.preventDefault();
          handleSelectCommit(safeIndex - 1);
          break;
        case "ArrowRight":
          event.preventDefault();
          handleSelectCommit(safeIndex + 1);
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
          handleSelectCommit(effectiveCommits.length - 1);
          break;
        case "Escape":
          if (event.target instanceof HTMLElement) {
            event.target.blur();
          }
          break;
      }
    },
    [safeIndex, effectiveCommits.length, handleSelectCommit, handleTogglePlay],
  );

  /** Auto-focus via ref callback — no useEffect needed */
  const containerCallbackRef = useCallback(
    (node: HTMLDivElement | null) => {
      containerRef.current = node;
      node?.focus();
    },
    [],
  );

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
      ref={containerCallbackRef}
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
          {/* Commits / Turns toggle */}
          <div className="flex items-center gap-1 border-b border-envoi-border px-3.5 py-1.5">
            <button
              onClick={() => setGroupByTurn(false)}
              className={`shrink-0 rounded-full px-2 py-0.5 text-[13px] font-semibold transition-colors ${
                !groupByTurn
                  ? "bg-envoi-text text-white"
                  : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
              }`}
            >
              commits
            </button>
            <button
              onClick={() => setGroupByTurn(true)}
              className={`shrink-0 rounded-full px-2 py-0.5 text-[13px] font-semibold transition-colors ${
                groupByTurn
                  ? "bg-envoi-text text-white"
                  : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
              }`}
            >
              turns
            </button>
          </div>

          {/* Progress curve */}
          <ProgressCurve
            commits={effectiveCommits}
            selectedIndex={safeIndex}
            onSelect={handleSelectCommit}
            activeSuite={activeSuite}
            suites={suites}
            totalTests={totalTests}
          />

          {/* Playback controls */}
          <PlayControls
            totalCommits={effectiveCommits.length}
            selectedIndex={safeIndex}
            onSelect={handleSelectCommit}
            isPlaying={isPlaying}
            onTogglePlay={handleTogglePlay}
            speed={speed}
            onSpeedChange={handleSpeedChange}
          />

          {/* Suite filter pills + critical filter toggle */}
          <div className="flex flex-nowrap items-center gap-1 overflow-x-auto border-b border-envoi-border px-3.5 py-1.5">
            {isLive && <LiveBadge />}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={() => setActiveSuite("all")}
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[13px] font-semibold transition-colors ${
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
                    className={`shrink-0 rounded-full px-2 py-0.5 text-[13px] font-semibold transition-colors ${
                      activeSuite === suite.name
                        ? "bg-envoi-text text-white"
                        : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
                    }`}
                  >
                    {suite.name}
                  </button>
                </TooltipTrigger>
                <TooltipContent>
                  {suite.name}: {suite.total} tests
                </TooltipContent>
              </Tooltip>
            ))}

            {/* Separator + critical filter */}
            {criticalityMap.size > 0 && (
              <>
                <div className="mx-1 h-3.5 w-px bg-envoi-border-light" />
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setShowCriticalOnly((prev) => !prev)}
                      className={`shrink-0 rounded-full px-2 py-0.5 text-[13px] font-semibold transition-colors ${
                        showCriticalOnly
                          ? "bg-envoi-accent text-white"
                          : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
                      }`}
                    >
                      critical ({criticalityMap.size})
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>
                    Show only critical commits (large deltas, suite transitions,
                    regression recoveries)
                  </TooltipContent>
                </Tooltip>
              </>
            )}

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
            {visibleCommits.map((commit) => {
              const commitIdx = effectiveCommits.indexOf(commit);
              const prevCommit = commitIdx > 0 ? effectiveCommits[commitIdx - 1] : undefined;
              const elapsedSincePrev = prevCommit !== undefined
                ? commit.minutesElapsed - prevCommit.minutesElapsed
                : undefined;
              return (
                <CommitRow
                  key={commit.index}
                  commit={commit}
                  isSelected={commitIdx === safeIndex}
                  onSelect={() => handleSelectCommit(commitIdx)}
                  activeSuite={activeSuite}
                  suites={suites}
                  criticalityTags={criticalityMap.get(commitIdx)}
                  elapsedSincePrev={elapsedSincePrev}
                  prevCommit={prevCommit}
                />
              );
            })}
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
          className={`relative z-10 h-9 w-1 rounded-full transition-colors ${
            dragging ? "" : "bg-envoi-border group-hover:bg-envoi-accent"
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
        <div className="flex h-10.25 shrink-0 items-stretch border-b border-envoi-border">
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
        ) : (
          <TestsPanel
            commit={selectedCommit}
            suites={suites}
            totalTests={totalTests}
          />
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
      className={`flex items-center px-3.5 text-[12px] font-semibold uppercase tracking-[0.06em] transition-colors ${
        isActive
          ? "border-b-2 border-envoi-accent text-envoi-accent"
          : "border-b-2 border-transparent text-envoi-text-dim hover:text-envoi-text"
      }`}
    >
      {label}
    </button>
  );
}

/** Pulsing green dot + LIVE label shown when the trajectory is still running */
function LiveBadge() {
  return (
    <span className="mr-1 flex shrink-0 items-center gap-1.25 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.08em] text-emerald-700">
      <span className="relative flex h-1.75 w-1.75">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
        <span className="relative inline-flex h-1.75 w-1.75 rounded-full bg-emerald-500" />
      </span>
      live
    </span>
  );
}
