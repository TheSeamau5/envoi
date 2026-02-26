/**
 * Main client component for the Trajectory Detail page.
 * 42/58 split layout: left panel (Timeline / Tests & Metrics) and right panel (Steps / Code).
 *
 * Manages all interactive state: selected commit, playback, tabs, suite filter.
 */

"use client";

import { useState, useCallback } from "react";
import type { Trajectory, DetailLeftTab, DetailRightTab } from "@/lib/types";
import { SUITES } from "@/lib/constants";
import { ProgressCurve } from "./progress-curve";
import { PlayControls } from "./play-controls";
import { CommitRow } from "./commit-row";
import { TestsPanel } from "./tests-panel";
import { StepsPanel } from "./steps-panel";
import { CodePanel } from "./code-panel";

type TrajectoryDetailProps = {
  trajectory: Trajectory;
};

export function TrajectoryDetail({ trajectory }: TrajectoryDetailProps) {
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

  if (!selectedCommit) return undefined;

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left panel — 42% */}
      <div className="flex flex-col overflow-hidden border-r border-envoi-border" style={{ width: "42%" }}>
        {/* Tab bar */}
        <div className="flex h-[41px] shrink-0 border-b border-envoi-border">
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
              <SuitePill
                label="all"
                isActive={activeSuite === "all"}
                onClick={() => setActiveSuite("all")}
              />
              {SUITES.map((suite) => (
                <SuitePill
                  key={suite.name}
                  label={suite.name}
                  isActive={activeSuite === suite.name}
                  onClick={() => setActiveSuite(suite.name)}
                />
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

      {/* Right panel — 58% */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Tab bar */}
        <div className="flex h-[41px] shrink-0 border-b border-envoi-border">
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
        </div>

        {/* Right tab content */}
        {rightTab === "steps" ? (
          <StepsPanel commit={selectedCommit} />
        ) : (
          <CodePanel commit={selectedCommit} />
        )}
      </div>
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

/** Suite filter pill */
function SuitePill({
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
      className={`rounded-full px-[8px] py-[2px] text-[9px] font-semibold transition-colors ${
        isActive
          ? "bg-envoi-text text-white"
          : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light hover:text-envoi-text"
      }`}
    >
      {label}
    </button>
  );
}
