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

/** Available model identifiers in agent/model format */
export const MODELS = [
  "claude-code/opus-4.6",
  "claude-code/sonnet-4.6",
  "codex/gpt-5.3-codex",
  "opencode/glm-5",
  "opencode/minimax-m2.5",
] as const;

/** Key milestones tracked across trajectories.
 *  Ordered: overall first, then per-suite. Each group has >25%, >50%, >90%, 100%. */
export const MILESTONES: MilestoneDef[] = [
  // overall
  { id: "o25",  label: ">25%",  suite: undefined,       threshold: Math.ceil(TOTAL_TESTS * 0.25), group: "overall" },
  { id: "o50",  label: ">50%",  suite: undefined,       threshold: Math.ceil(TOTAL_TESTS * 0.50), group: "overall" },
  { id: "o90",  label: ">90%",  suite: undefined,       threshold: Math.ceil(TOTAL_TESTS * 0.90), group: "overall" },
  { id: "o100", label: "100%",  suite: undefined,       threshold: TOTAL_TESTS,                   group: "overall" },
  // basics
  { id: "b25",  label: ">25%",  suite: "basics",        threshold: Math.ceil(35 * 0.25),  group: "basics" },
  { id: "b50",  label: ">50%",  suite: "basics",        threshold: Math.ceil(35 * 0.50),  group: "basics" },
  { id: "b90",  label: ">90%",  suite: "basics",        threshold: Math.ceil(35 * 0.90),  group: "basics" },
  { id: "b100", label: "100%",  suite: "basics",        threshold: 35,                    group: "basics" },
  // wacct
  { id: "w25",  label: ">25%",  suite: "wacct",         threshold: Math.ceil(1559 * 0.25), group: "wacct" },
  { id: "w50",  label: ">50%",  suite: "wacct",         threshold: Math.ceil(1559 * 0.50), group: "wacct" },
  { id: "w90",  label: ">90%",  suite: "wacct",         threshold: Math.ceil(1559 * 0.90), group: "wacct" },
  { id: "w100", label: "100%",  suite: "wacct",         threshold: 1559,                   group: "wacct" },
  // c_testsuite
  { id: "c25",  label: ">25%",  suite: "c_testsuite",   threshold: Math.ceil(220 * 0.25),  group: "c_testsuite" },
  { id: "c50",  label: ">50%",  suite: "c_testsuite",   threshold: Math.ceil(220 * 0.50),  group: "c_testsuite" },
  { id: "c90",  label: ">90%",  suite: "c_testsuite",   threshold: Math.ceil(220 * 0.90),  group: "c_testsuite" },
  { id: "c100", label: "100%",  suite: "c_testsuite",   threshold: 220,                    group: "c_testsuite" },
  // torture
  { id: "t25",  label: ">25%",  suite: "torture",       threshold: Math.ceil(370 * 0.25),  group: "torture" },
  { id: "t50",  label: ">50%",  suite: "torture",       threshold: Math.ceil(370 * 0.50),  group: "torture" },
  { id: "t90",  label: ">90%",  suite: "torture",       threshold: Math.ceil(370 * 0.90),  group: "torture" },
  { id: "t100", label: "100%",  suite: "torture",       threshold: 370,                    group: "torture" },
];

/** Dimensions available for grouping trajectories in Setup Compare mode */
export const GROUPABLE_DIMENSIONS = [
  { key: "model", label: "Model" },
  { key: "implLang", label: "Programming Language" },
  { key: "lang", label: "Natural Language" },
  { key: "target", label: "Target" },
  { key: "milestone", label: "Milestone" },
  { key: "agent", label: "Agent" },
  { key: "sandbox", label: "Sandbox" },
] as const;

/** Maximum duration in minutes for chart X axis */
export const MAX_DURATION = 480;
