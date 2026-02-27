/**
 * Mock data generation constants: model configs, step type distributions,
 * error message pools, reasoning text pools.
 */

/** Per-model configuration for trajectory generation */
export type ModelConfig = {
  model: string;
  traceCount: number;
  commitRange: [number, number];
  durationRange: [number, number];
  speedFactor: number;
  plateauFactor: number;
  regressionProbability: number;
  finalCeilingRange: [number, number];
  costPerMTok: number;
  tokenRange: [number, number];
};

export const MODEL_CONFIGS: ModelConfig[] = [
  {
    model: "claude-code/opus-4.6",
    traceCount: 6,
    commitRange: [25, 45],
    durationRange: [480, 480],
    speedFactor: 0.9,
    plateauFactor: 0.9,
    regressionProbability: 0.12,
    finalCeilingRange: [0.45, 0.65],
    costPerMTok: 5.2,
    tokenRange: [8_000_000, 20_000_000],
  },
  {
    model: "claude-code/sonnet-4.6",
    traceCount: 6,
    commitRange: [30, 50],
    durationRange: [480, 480],
    speedFactor: 0.95,
    plateauFactor: 0.95,
    regressionProbability: 0.08,
    finalCeilingRange: [0.35, 0.55],
    costPerMTok: 1.8,
    tokenRange: [6_000_000, 15_000_000],
  },
  {
    model: "codex/gpt-5.3-codex",
    traceCount: 6,
    commitRange: [30, 55],
    durationRange: [480, 480],
    speedFactor: 1.0,
    plateauFactor: 1.05,
    regressionProbability: 0.15,
    finalCeilingRange: [0.40, 0.55],
    costPerMTok: 3.8,
    tokenRange: [8_000_000, 22_000_000],
  },
  {
    model: "opencode/glm-5",
    traceCount: 6,
    commitRange: [30, 55],
    durationRange: [480, 480],
    speedFactor: 0.85,
    plateauFactor: 0.85,
    regressionProbability: 0.1,
    finalCeilingRange: [0.50, 0.70],
    costPerMTok: 2.1,
    tokenRange: [7_000_000, 18_000_000],
  },
  {
    model: "opencode/minimax-m2.5",
    traceCount: 6,
    commitRange: [45, 70],
    durationRange: [480, 480],
    speedFactor: 1.2,
    plateauFactor: 1.25,
    regressionProbability: 0.12,
    finalCeilingRange: [0.30, 0.45],
    costPerMTok: 1.2,
    tokenRange: [10_000_000, 25_000_000],
  },
];

/** Impl languages and their probabilities */
export const IMPL_LANGS = ["rust", "rust", "rust", "zig", "zig"] as const;

/** Natural language options (weighted towards English) */
export const NATURAL_LANGS = ["english", "english", "english", "spanish", "arabic", "mandarin chinese"] as const;

/** Sandbox options */
export const SANDBOXES = ["modal", "modal", "modal", "e2b"] as const;

/** Agent options */
export const AGENTS = ["codex", "codex", "opencode"] as const;

/** Milestone options */
export const MILESTONE_STARTS = ["M0", "M0", "M0", "M0", "M1"] as const;

/** Error messages for broken tests */
export const ERROR_MESSAGES = [
  "Segmentation fault (core dumped)",
  "Expected exit code 0, got 1",
  "Expected output '42', got '0'",
  "Compilation error: undefined symbol '_main'",
  "Timeout after 30s",
  "Expected output 'hello world', got ''",
  "Wrong exit code: expected 0, got 139",
  "error: linking failed with exit code 1",
] as const;

/** Reasoning summaries used for the first step of commits */
export const REASONING_SUMMARIES = [
  "Analyzing test failures in basics suite",
  "Planning type coercion implementation",
  "Planning pointer arithmetic support",
  "Planning struct layout implementation",
  "Planning switch statement codegen",
  "Reviewing error: segfault in generated code",
  "Reviewing error: incorrect register allocation",
  "Reviewing error: stack misalignment at function call",
  "Investigating why previously-passing tests now fail",
  "Analyzing wacct chapter progression",
  "Planning expression parser refactor",
  "Designing intermediate representation for optimization",
  "Analyzing c_testsuite failures for standards compliance",
  "Reviewing torture test edge cases",
  "Planning register allocator redesign",
] as const;

/** Secondary reasoning summaries */
export const REASONING_SECONDARY = [
  "Considering alternative approach for code generation",
  "Tracing through failing test case step by step",
  "Checking C standard for correct behavior",
  "Reviewing x86_64 calling convention requirements",
  "Analyzing impact of optimization on test results",
] as const;

/** Source file paths for read/write steps */
export const SOURCE_FILES = [
  "src/main.rs",
  "src/lexer.rs",
  "src/parser.rs",
  "src/codegen.rs",
  "src/types.rs",
  "src/ast.rs",
] as const;

/** Tool call summaries */
export const TOOL_CALLS = [
  "cargo build",
  "cargo test",
  "./run_tests.sh basics",
  "./cc test_input.c -o test_out",
] as const;

/** Suite names for test run steps */
export const TEST_SUITE_NAMES = ["basics", "wacct", "c_testsuite", "torture"] as const;
