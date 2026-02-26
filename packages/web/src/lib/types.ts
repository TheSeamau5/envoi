/**
 * Core TypeScript type definitions for the Envoi dashboard.
 *
 * Rules:
 * - Always `type`, never `interface`
 * - Always `undefined`, never `null`
 * - FileSnapshot.added uses number[] (not Set) for server/client serialization
 */

/** A test suite within an environment (e.g., "basics" with 35 tests) */
export type Suite = {
  name: string;
  total: number;
};

/** Per-suite pass counts at a point in time */
export type SuiteState = Record<string, number>;

/** A test that broke in this commit compared to the previous one */
export type BrokenTest = {
  suite: string;
  testId: string;
  error: string;
};

/** Turn-end evaluation results produced by the envoi server */
export type Evaluation = {
  passedDelta: number;
  newlyBroken: number;
  newlyFixed: number;
  brokenTests: BrokenTest[];
  totalPassed: number;
  totalFailed: number;
};

/** One agent action between two commits */
export type Step = {
  type: "reasoning" | "file_read" | "file_write" | "tool_call" | "test_run" | "mcp_call";
  summary: string;
  detail: string;
  index: number;
  durationMs?: number;
  tokensUsed?: number;
  /** JSON string of tool arguments (tool_call, mcp_call, file_read, file_write) */
  toolInput?: string;
  /** Tool response text */
  toolOutput?: string;
  /** Whether this step resulted in an error */
  isError?: boolean;
  /** Error details when isError is true */
  errorMessage?: string;
  /** Full reasoning/thinking trace (reasoning steps) */
  reasoningContent?: string;
  /** Agent's plan text (reasoning steps that contain a plan) */
  planContent?: string;
};

/** A file that was modified in a commit */
export type ChangedFile = {
  path: string;
  additions: number;
  deletions: number;
  isNew: boolean;
};

/** Snapshot of a single file's content at a specific commit */
export type FileSnapshot = {
  lines: string[];
  added: number[];
  touched: boolean;
  isNew?: boolean;
};

/** All file snapshots at a commit — keyed by file path */
export type CodeSnapshot = Record<string, FileSnapshot>;

/** A git checkpoint with evaluation results and agent steps */
export type Commit = {
  index: number;
  hash: string;
  turn: number;
  timestamp: string;
  minutesElapsed: number;
  suiteState: SuiteState;
  totalPassed: number;
  delta: number;
  isRegression: boolean;
  isMilestone: boolean;
  milestoneLabel?: string;
  feedback: Evaluation;
  steps: Step[];
  changedFiles: ChangedFile[];
  codeSnapshot: CodeSnapshot;
  phase: number;
  tokensUsed: number;
};

/** Configuration parameters for a trajectory run */
export type TrajectoryParams = {
  target: string;
  implLang: string;
  lang: string;
  milestone: string;
  sandbox: string;
  agent: string;
};

/** A complete agent trajectory — one attempt at solving the environment */
export type Trajectory = {
  id: string;
  model: string;
  environment: string;
  commits: Commit[];
  totalTests: number;
  startedAt: string;
  duration: string;
  totalTokens: number;
  cost: number;
  params: TrajectoryParams;
  finalPassed: number;
};

/** A group of trajectories sharing some dimension */
export type TrajectoryGroup = {
  key: string;
  label: string;
  traces: Trajectory[];
};

/** Definition of a milestone threshold */
export type MilestoneDef = {
  id: string;
  label: string;
  suite: string | undefined;
  threshold: number;
};

/** Compare page mode */
export type CompareMode = "traces" | "setups";

/** Compare tab selection */
export type CompareTab = "curves" | "milestones" | "suites";

/** Trajectory detail left panel tab */
export type DetailLeftTab = "timeline" | "tests";

/** Trajectory detail right panel tab */
export type DetailRightTab = "steps" | "code";
