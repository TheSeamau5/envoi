/**
 * Playback transport bar with speed controls.
 * Client component — manages play/pause state and auto-advance timer.
 *
 * Transport buttons: SkipBack, ChevronLeft, Play/Pause, ChevronRight, SkipForward
 * Speed selectors: 0.5x, 1x, 2x, 4x
 * Position indicator: current / total
 */

"use client";

import { useEffect, useRef, useCallback } from "react";
import {
  SkipBack,
  ChevronLeft,
  Play,
  Pause,
  ChevronRight,
  SkipForward,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type PlayControlsProps = {
  totalCommits: number;
  selectedIndex: number;
  onSelect: (index: number) => void;
  isPlaying: boolean;
  onTogglePlay: () => void;
  speed: number;
  onSpeedChange: (speed: number) => void;
};

const SPEED_OPTIONS = [0.5, 1, 2, 4] as const;

export function PlayControls({
  totalCommits,
  selectedIndex,
  onSelect,
  isPlaying,
  onTogglePlay,
  speed,
  onSpeedChange,
}: PlayControlsProps) {
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const advanceCommit = useCallback(() => {
    onSelect(selectedIndex + 1);
  }, [selectedIndex, onSelect]);

  useEffect(() => {
    if (isPlaying && selectedIndex < totalCommits - 1) {
      const intervalMs = Math.round(1000 / speed);
      intervalRef.current = setInterval(advanceCommit, intervalMs);
    }
    return () => {
      if (intervalRef.current !== undefined) {
        clearInterval(intervalRef.current);
        intervalRef.current = undefined;
      }
    };
  }, [isPlaying, selectedIndex, totalCommits, speed, advanceCommit]);

  /** Stop playback when reaching the end */
  useEffect(() => {
    if (isPlaying && selectedIndex >= totalCommits - 1) {
      onTogglePlay();
    }
  }, [isPlaying, selectedIndex, totalCommits, onTogglePlay]);

  const canGoBack = selectedIndex > 0;
  const canGoForward = selectedIndex < totalCommits - 1;

  return (
    <div className="flex items-center gap-3 border-b border-envoi-border px-[14px] py-[8px]">
      {/* Transport buttons */}
      <div className="flex items-center gap-[2px]">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => onSelect(0)}
              disabled={!canGoBack}
              className="flex h-[24px] w-[24px] items-center justify-center rounded text-envoi-text-muted transition-colors hover:bg-envoi-surface disabled:opacity-30"
            >
              <SkipBack size={12} />
            </button>
          </TooltipTrigger>
          <TooltipContent>First commit</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => onSelect(Math.max(0, selectedIndex - 1))}
              disabled={!canGoBack}
              className="flex h-[24px] w-[24px] items-center justify-center rounded text-envoi-text-muted transition-colors hover:bg-envoi-surface disabled:opacity-30"
            >
              <ChevronLeft size={14} />
            </button>
          </TooltipTrigger>
          <TooltipContent>Previous commit</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={onTogglePlay}
              className="flex h-[26px] w-[26px] items-center justify-center rounded bg-envoi-accent-bg text-envoi-accent transition-colors hover:bg-envoi-accent hover:text-white"
            >
              {isPlaying ? <Pause size={12} /> : <Play size={12} />}
            </button>
          </TooltipTrigger>
          <TooltipContent>{isPlaying ? "Pause" : "Play"}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => onSelect(Math.min(totalCommits - 1, selectedIndex + 1))}
              disabled={!canGoForward}
              className="flex h-[24px] w-[24px] items-center justify-center rounded text-envoi-text-muted transition-colors hover:bg-envoi-surface disabled:opacity-30"
            >
              <ChevronRight size={14} />
            </button>
          </TooltipTrigger>
          <TooltipContent>Next commit</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => onSelect(totalCommits - 1)}
              disabled={!canGoForward}
              className="flex h-[24px] w-[24px] items-center justify-center rounded text-envoi-text-muted transition-colors hover:bg-envoi-surface disabled:opacity-30"
            >
              <SkipForward size={12} />
            </button>
          </TooltipTrigger>
          <TooltipContent>Last commit</TooltipContent>
        </Tooltip>
      </div>

      {/* Speed selectors */}
      <div className="flex items-center gap-[2px]">
        {SPEED_OPTIONS.map((speedOption) => (
          <button
            key={speedOption}
            onClick={() => onSpeedChange(speedOption)}
            className={`rounded px-[6px] py-[2px] text-[9px] font-semibold transition-colors ${
              speed === speedOption
                ? "bg-envoi-text text-white"
                : "text-envoi-text-dim hover:bg-envoi-surface hover:text-envoi-text"
            }`}
          >
            {speedOption}×
          </button>
        ))}
      </div>

      {/* Position indicator */}
      <div className="flex-1" />
      <span className="text-[10px] text-envoi-text-dim">
        {selectedIndex + 1} / {totalCommits}
      </span>
    </div>
  );
}
