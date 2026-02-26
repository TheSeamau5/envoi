/**
 * Application constants: suites, milestones, models, grouping dimensions.
 * Shared across server and client components.
 */

import type { Suite, MilestoneDef } from "./types";

/** The four test suites in the C compiler environment */
export const SUITES: Suite[] = [
  { name: "basics", total: 35 },
  { name: "wacct", total: 1559 },
  { name: "c_testsuite", total: 220 },
  { name: "torture", total: 370 },
];

/** Total number of tests across all suites */
export const TOTAL_TESTS = SUITES.reduce((sum, suite) => sum + suite.total, 0);

/** Available model identifiers for trajectory generation */
export const MODELS = [
  "claude-opus-4-5-20250514",
  "claude-sonnet-4-5-20250514",
  "deepseek-v3",
  "gpt-5.3-codex",
  "o3-2025-04-16",
  "qwen3-32b",
] as const;

/** Key milestones tracked across trajectories */
export const MILESTONES: MilestoneDef[] = [
  { id: "b50", label: "basics \u226550%", suite: "basics", threshold: 18 },
  { id: "b100", label: "basics 100%", suite: "basics", threshold: 35 },
  { id: "w25", label: "wacct \u226525%", suite: "wacct", threshold: 390 },
  { id: "w50", label: "wacct \u226550%", suite: "wacct", threshold: 780 },
  { id: "c25", label: "c_testsuite \u226525%", suite: "c_testsuite", threshold: 55 },
  { id: "c50", label: "c_testsuite \u226550%", suite: "c_testsuite", threshold: 110 },
  { id: "t1", label: "torture >0%", suite: "torture", threshold: 1 },
  { id: "t10", label: "torture \u226510%", suite: "torture", threshold: 37 },
  { id: "o25", label: "overall \u226525%", suite: undefined, threshold: 546 },
  { id: "o50", label: "overall \u226550%", suite: undefined, threshold: 1092 },
];

/** Dimensions available for grouping trajectories in Setup Compare mode */
export const GROUPABLE_DIMENSIONS = [
  { key: "model", label: "Model" },
  { key: "implLang", label: "Impl Language" },
  { key: "target", label: "Target" },
  { key: "milestone", label: "Milestone" },
  { key: "agent", label: "Agent" },
  { key: "sandbox", label: "Sandbox" },
] as const;

/** Maximum duration in minutes for chart X axis */
export const MAX_DURATION = 480;
