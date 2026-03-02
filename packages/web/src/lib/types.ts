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
  type:
    | "reasoning"
    | "file_read"
    | "file_write"
    | "tool_call"
    | "test_run"
    | "mcp_call"
    | "text"
    | "spawn"
    | "message";
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
  /** Global part range [start, end] from the parquet — used to map waste items to commits */
  partRange?: [number, number];
  /** Evaluation ID from the backend */
  evalId?: string;
  /** Git commit hash this evaluation targeted */
  targetCommit?: string;
};

/** Configuration parameters for a trajectory run (flexible for any environment) */
export type TrajectoryParams = Record<string, string>;

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
  /** Dynamic suite definitions from the data (replaces hardcoded SUITES) */
  suites?: Suite[];
  /** Agent harness name (e.g. "codex", "claude-code") */
  agentHarness?: string;
  /** Session identifier */
  sessionId?: string;
  /** Why the session ended (e.g. "solved", "timeout", "agent_error") */
  sessionEndReason?: string;
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
  suite?: string;
  threshold: number;
  /** Grouping key for table column separators (e.g. "overall", "basics") */
  group: string;
};

/** Row in the difficulty heatmap — one per (environment, test_category, model) */
export type DifficultyCell = {
  environment: string;
  category: string;
  model: string;
  passRate: number;
  attempts: number;
};

/** Row in the portfolio dashboard — one per model */
export type PortfolioRow = {
  model: string;
  environments: Record<string, { passRate: number; rank: number }>;
  avgRank: number;
};

/** Schema column info for the SQL console */
export type SchemaColumn = {
  tableName: string;
  columnName: string;
  dataType: string;
};

// ---------------------------------------------------------------------------
// Portfolio enrichment (per-environment summaries and Pareto frontier)
// ---------------------------------------------------------------------------

/** Per-environment summary row for the enriched portfolio dashboard */
export type PortfolioEnvironmentRow = {
  environment: string;
  bestPassed: number;
  bestTotal: number;
  bestModel: string;
  medianPassRate: number;
  runCount: number;
  totalTokens: number;
  perModelCounts: Record<string, number>;
};

/** A single trajectory point for the Pareto frontier scatter plot */
export type ParetoPoint = {
  trajectoryId: string;
  model: string;
  environment: string;
  totalTokens: number;
  passed: number;
  total: number;
  passRate: number;
};

// ---------------------------------------------------------------------------
// Query templates
// ---------------------------------------------------------------------------

/** Query template parameter definition */
export type TemplateParameter = {
  name: string;
  label: string;
  type: "text" | "select";
  defaultValue: string;
  options?: string[];
};

/** Predefined query template */
export type QueryTemplate = {
  id: string;
  name: string;
  description: string;
  sql: string;
  parameters: TemplateParameter[];
  visualization: "table" | "bar" | "line";
};
