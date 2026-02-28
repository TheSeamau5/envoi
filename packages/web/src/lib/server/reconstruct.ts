/**
 * Trajectory reconstruction: Parquet rows → UI types.
 *
 * Translates flat one-row-per-part data from the Parquet schema into the
 * hierarchical Trajectory/Commit/Step model that the frontend expects.
 */

import type {
  Trajectory,
  Commit,
  Step,
  ChangedFile,
  SuiteState,
  Suite,
} from "@/lib/types";
import { computeTotalTests } from "@/lib/constants";

// ---------------------------------------------------------------------------
// Raw Parquet row type (subset of fields we actually use)
// ---------------------------------------------------------------------------

export type ParquetRow = {
  trajectory_id: string;
  session_id: string | undefined;
  agent: string | undefined;
  agent_model: string | undefined;
  started_at: string | undefined;
  environment: string | undefined;
  task_params: string | undefined;
  part: number;
  timestamp: string | undefined;
  role: string | undefined;
  part_type: string | undefined;
  item_type: string | undefined;
  summary: string | undefined;
  duration_ms: number | bigint | undefined;
  git_commit: string | undefined;
  content: string | undefined;
  content_token_estimate: number | undefined;
  tool_name: string | undefined;
  tool_status: string | undefined;
  tool_input: string | undefined;
  tool_output: string | undefined;
  tool_error: string | undefined;
  tool_exit_code: number | undefined;
  token_usage: string | undefined;
  patch: string | undefined;
  repo_checkpoint: string | undefined;
  testing_state: string | undefined;
  eval_events_delta: string | undefined;
  turn: number | undefined;
  session_end_reason: string | undefined;
  session_end_total_parts: number | undefined;
  session_end_total_turns: number | undefined;
  session_end_final_commit: string | undefined;
  suites: string | undefined;
  files: string | undefined;
  bundle_uri: string | undefined;
};

// ---------------------------------------------------------------------------
// JSON parsing helpers
// ---------------------------------------------------------------------------

function parseJson(value: string | undefined): unknown {
  if (!value) return undefined;
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

/** Type guard: narrows unknown to a plain object (Record<string, unknown>) */
function isRecord(val: unknown): val is Record<string, unknown> {
  return typeof val === "object" && val !== null && !Array.isArray(val);
}

function toNumber(val: number | bigint | undefined): number {
  if (val === undefined || val === null) return 0;
  return typeof val === "bigint" ? Number(val) : val;
}

// ---------------------------------------------------------------------------
// Evaluation reconstruction (port of Python build_evaluations_from_parts)
// ---------------------------------------------------------------------------

type EvalRecord = {
  evalId: string;
  commit: string;
  part: number;
  triggerTurn: number | undefined;
  status: string;
  passed: number;
  failed: number;
  total: number;
  suiteResults: Record<string, { passed: number; total: number }>;
  tests: unknown[];
};

function buildEvaluationsFromRows(
  rows: ParquetRow[],
): Map<string, EvalRecord> {
  const evaluations = new Map<string, EvalRecord>();

  for (const row of rows) {
    const events = parseJson(row.eval_events_delta);
    if (!Array.isArray(events)) continue;

    for (const event of events) {
      if (typeof event !== "object" || event === null) continue;
      if (event.kind !== "commit_async") continue;
      const commit = event.target_commit;
      if (typeof commit !== "string" || !commit) continue;

      const triggerPart =
        typeof event.trigger_part === "number"
          ? event.trigger_part
          : typeof row.part === "number"
            ? row.part
            : 0;

      if (!evaluations.has(commit)) {
        evaluations.set(commit, {
          evalId:
            typeof event.eval_id === "string" && event.eval_id
              ? event.eval_id
              : `recovered-${commit.slice(0, 12)}-${triggerPart}`,
          commit,
          part: triggerPart,
          triggerTurn:
            typeof event.trigger_turn === "number"
              ? event.trigger_turn
              : undefined,
          status: "queued",
          passed: 0,
          failed: 0,
          total: 0,
          suiteResults: {},
          tests: [],
        });
      }

      const rec = evaluations.get(commit);
      if (!rec) continue;

      if (typeof event.status === "string" && event.status) {
        rec.status = event.status;
      }
      if (typeof event.eval_id === "string") rec.evalId = event.eval_id;
      if (typeof event.trigger_turn === "number")
        rec.triggerTurn = event.trigger_turn;
      if (typeof event.passed === "number") rec.passed = event.passed;
      if (typeof event.failed === "number") rec.failed = event.failed;
      if (typeof event.total === "number") rec.total = event.total;
      if (typeof event.suite_results === "object" && event.suite_results) {
        rec.suiteResults = event.suite_results;
      }
      if (Array.isArray(event.tests)) rec.tests = event.tests;
    }
  }

  return evaluations;
}

// ---------------------------------------------------------------------------
// Step type mapping
// ---------------------------------------------------------------------------

function mapPartType(
  partType: string | undefined,
  _itemType: string | undefined,
  toolName: string | undefined,
): Step["type"] {
  if (partType === "reasoning") return "reasoning";
  if (partType === "text") return "text";

  if (partType === "function_call" || partType === "tool_call") {
    const name = (toolName ?? "").toLowerCase();
    if (name.includes("read") || name.includes("cat")) return "file_read";
    if (
      name.includes("write") ||
      name.includes("edit") ||
      name.includes("create") ||
      name.includes("patch")
    )
      return "file_write";
    if (
      name.includes("test") ||
      name.includes("envoi") ||
      name.includes("eval")
    )
      return "test_run";
    if (name.includes("mcp") || name.includes("server")) return "mcp_call";
    return "tool_call";
  }

  return "tool_call";
}

function rowToStep(row: ParquetRow, index: number): Step {
  const stepType = mapPartType(row.part_type, row.item_type, row.tool_name);
  return {
    type: stepType,
    summary: row.summary ?? "",
    detail: row.content ?? "",
    index,
    durationMs: toNumber(row.duration_ms) || undefined,
    tokensUsed: row.content_token_estimate ?? undefined,
    toolInput: row.tool_input ?? undefined,
    toolOutput: row.tool_output ?? undefined,
    isError:
      row.tool_status === "error" ||
      (row.tool_exit_code !== undefined &&
        row.tool_exit_code !== null &&
        row.tool_exit_code !== 0),
    errorMessage: row.tool_error ?? undefined,
    reasoningContent:
      row.part_type === "reasoning" ? row.content ?? undefined : undefined,
  };
}

// ---------------------------------------------------------------------------
// Changed files from repo_checkpoint
// ---------------------------------------------------------------------------

function extractChangedFiles(
  checkpoint: unknown,
): ChangedFile[] {
  if (!isRecord(checkpoint)) return [];
  const cp = checkpoint;
  const files = cp.changed_files;
  const numstat = cp.numstat;

  if (Array.isArray(files)) {
    const numstatMap = new Map<
      string,
      { additions: number; deletions: number }
    >();
    if (Array.isArray(numstat)) {
      for (const entry of numstat) {
        if (isRecord(entry)) {
          const e = entry;
          if (typeof e.path === "string") {
            numstatMap.set(e.path, {
              additions: typeof e.additions === "number" ? e.additions : 0,
              deletions: typeof e.deletions === "number" ? e.deletions : 0,
            });
          }
        }
      }
    }
    return files
      .filter((f): f is string => typeof f === "string")
      .map((path) => {
        const stats = numstatMap.get(path);
        return {
          path,
          additions: stats?.additions ?? 0,
          deletions: stats?.deletions ?? 0,
          isNew: false,
        };
      });
  }
  return [];
}

// ---------------------------------------------------------------------------
// Suite state from evaluation's suite_results
// ---------------------------------------------------------------------------

function buildSuiteState(
  suiteResults: Record<string, { passed: number; total: number }>,
): SuiteState {
  const state: SuiteState = {};
  for (const [suiteName, result] of Object.entries(suiteResults)) {
    state[suiteName] =
      typeof result === "object" && result !== null
        ? typeof result.passed === "number"
          ? result.passed
          : 0
        : typeof result === "number"
          ? result
          : 0;
  }
  return state;
}

// ---------------------------------------------------------------------------
// Parse suites from the Parquet data
// ---------------------------------------------------------------------------

export function parseSuites(suitesJson: string | undefined): Suite[] {
  const parsed = parseJson(suitesJson);
  if (parsed === undefined || parsed === null) return [];

  // suites can be { "suiteName": { "total": N, ... }, ... } or an array
  if (Array.isArray(parsed)) {
    return parsed
      .filter(
        (s): s is { name: string; total: number } =>
          typeof s === "object" &&
          s !== null &&
          typeof s.name === "string" &&
          typeof s.total === "number",
      )
      .map((s) => ({ name: s.name, total: s.total }));
  }

  // Object form: { suiteName: { total: N, ... } }
  if (!isRecord(parsed)) return [];
  const entries = Object.entries(parsed);
  return entries
    .filter(
      (entry): entry is [string, Record<string, unknown>] =>
        typeof entry[1] === "object" && entry[1] !== null,
    )
    .map(([name, val]) => ({
      name,
      total: typeof val.total === "number" ? val.total : 0,
    }));
}

// ---------------------------------------------------------------------------
// Parse task_params
// ---------------------------------------------------------------------------

function parseTaskParams(
  taskParamsJson: string | undefined,
): Record<string, string> {
  const parsed = parseJson(taskParamsJson);
  if (!isRecord(parsed)) return {};
  const result: Record<string, string> = {};
  for (const [key, val] of Object.entries(parsed)) {
    result[key] = String(val ?? "");
  }
  return result;
}

// ---------------------------------------------------------------------------
// Main reconstruction: rows → Trajectory
// ---------------------------------------------------------------------------

export function reconstructTrajectory(rows: ParquetRow[]): Trajectory {
  const first = rows[0];
  if (!first) {
    throw new Error("Cannot reconstruct trajectory from empty rows");
  }
  const sortedRows = [...rows].sort((a, b) => a.part - b.part);

  // Parse trajectory-level fields
  const suites = parseSuites(first.suites);
  const totalTests = suites.length > 0 ? computeTotalTests(suites) : 0;
  const params = parseTaskParams(first.task_params);

  // Build evaluations
  const evalMap = buildEvaluationsFromRows(sortedRows);
  const completedEvals = [...evalMap.values()]
    .filter((e) => e.status === "completed")
    .sort((a, b) => a.part - b.part);

  // Build commits from evaluation boundaries
  const commits: Commit[] = [];
  let prevTotalPassed = 0;

  if (completedEvals.length === 0) {
    // No evaluations — create a single "commit" containing all steps
    const steps = sortedRows.map((r, i) => rowToStep(r, i));
    const lastSortedRow = sortedRows[sortedRows.length - 1];
    const lastCheckpoint = parseJson(lastSortedRow?.repo_checkpoint);
    commits.push({
      index: 0,
      hash: first.git_commit ?? "unknown",
      turn: first.turn ?? 0,
      timestamp: first.timestamp ?? first.started_at ?? "",
      minutesElapsed: 0,
      suiteState: {},
      totalPassed: 0,
      delta: 0,
      isRegression: false,
      isMilestone: false,
      feedback: {
        passedDelta: 0,
        newlyBroken: 0,
        newlyFixed: 0,
        brokenTests: [],
        totalPassed: 0,
        totalFailed: 0,
      },
      steps,
      changedFiles: extractChangedFiles(lastCheckpoint),
      codeSnapshot: {},
      phase: 0,
      tokensUsed: steps.reduce((sum, s) => sum + (s.tokensUsed ?? 0), 0),
    });
  } else {
    // Group rows by evaluation boundaries
    let rowCursor = 0;

    for (const evalRec of completedEvals) {
      // Collect rows from cursor up to and including the eval trigger part
      const commitRows: ParquetRow[] = [];
      while (rowCursor < sortedRows.length) {
        const currentRow = sortedRows[rowCursor];
        if (!currentRow) break;
        commitRows.push(currentRow);
        rowCursor++;
        // If we've passed the trigger part, stop
        if (currentRow.part >= evalRec.part) break;
      }

      const steps = commitRows.map((r, i) => rowToStep(r, i));
      const suiteState = buildSuiteState(evalRec.suiteResults);
      const delta = evalRec.passed - prevTotalPassed;

      // Compute minutes elapsed from trajectory start
      const startTime = new Date(first.started_at ?? "").getTime();
      const partTime = new Date(
        commitRows[commitRows.length - 1]?.timestamp ?? "",
      ).getTime();
      const commitIndex = commits.length;
      const minutesElapsed =
        isNaN(startTime) || isNaN(partTime)
          ? commitIndex * 10
          : Math.round((partTime - startTime) / 60000);

      // Changed files from last row's repo_checkpoint
      const lastRow = commitRows[commitRows.length - 1];
      const checkpoint = parseJson(lastRow?.repo_checkpoint);

      commits.push({
        index: commitIndex,
        hash: evalRec.commit,
        turn: evalRec.triggerTurn ?? commitRows[0]?.turn ?? commitIndex,
        timestamp:
          commitRows[commitRows.length - 1]?.timestamp ??
          first.started_at ??
          "",
        minutesElapsed: Math.max(0, minutesElapsed),
        suiteState,
        totalPassed: evalRec.passed,
        delta,
        isRegression: delta < 0,
        isMilestone: false,
        feedback: {
          passedDelta: delta,
          newlyBroken: delta < 0 ? Math.abs(delta) : 0,
          newlyFixed: delta > 0 ? delta : 0,
          brokenTests: [],
          totalPassed: evalRec.passed,
          totalFailed: evalRec.failed,
        },
        steps,
        changedFiles: extractChangedFiles(checkpoint),
        codeSnapshot: {},
        phase: totalTests > 0 ? evalRec.passed / totalTests : 0,
        tokensUsed: steps.reduce((sum, s) => sum + (s.tokensUsed ?? 0), 0),
        evalId: evalRec.evalId,
        targetCommit: evalRec.commit,
      });

      prevTotalPassed = evalRec.passed;
    }

    // Remaining rows after the last evaluation
    if (rowCursor < sortedRows.length) {
      const remainingRows = sortedRows.slice(rowCursor);
      const steps = remainingRows.map((r, i) => rowToStep(r, i));
      const lastCheckpoint = parseJson(
        remainingRows[remainingRows.length - 1]?.repo_checkpoint,
      );

      commits.push({
        index: commits.length,
        hash:
          remainingRows[remainingRows.length - 1]?.git_commit ?? "pending",
        turn: remainingRows[0]?.turn ?? commits.length,
        timestamp:
          remainingRows[remainingRows.length - 1]?.timestamp ??
          first.started_at ??
          "",
        minutesElapsed: commits[commits.length - 1]?.minutesElapsed ?? 0,
        suiteState: commits[commits.length - 1]?.suiteState ?? {},
        totalPassed: prevTotalPassed,
        delta: 0,
        isRegression: false,
        isMilestone: false,
        feedback: {
          passedDelta: 0,
          newlyBroken: 0,
          newlyFixed: 0,
          brokenTests: [],
          totalPassed: prevTotalPassed,
          totalFailed: 0,
        },
        steps,
        changedFiles: extractChangedFiles(lastCheckpoint),
        codeSnapshot: {},
        phase: totalTests > 0 ? prevTotalPassed / totalTests : 0,
        tokensUsed: steps.reduce((sum, s) => sum + (s.tokensUsed ?? 0), 0),
      });
    }
  }

  // Compute duration
  const lastCommit = commits[commits.length - 1];
  const durationMinutes = lastCommit?.minutesElapsed ?? 0;
  const hours = Math.floor(durationMinutes / 60);
  const mins = durationMinutes % 60;
  const duration =
    hours > 0 ? `${hours} hrs ${mins} min` : `${mins} min`;

  // Total tokens
  const totalTokens = sortedRows.reduce(
    (sum, r) => sum + (r.content_token_estimate ?? 0),
    0,
  );

  // Final passed from last completed evaluation
  const lastEval = completedEvals[completedEvals.length - 1];
  const finalPassed = lastEval ? lastEval.passed : 0;

  // Model string
  const agent = first.agent ?? "";
  const agentModel = first.agent_model ?? "";
  const model = agent ? `${agent}/${agentModel}` : agentModel;

  return {
    id: first.trajectory_id,
    model,
    environment: first.environment ?? "",
    commits,
    totalTests,
    startedAt: first.started_at ?? "",
    duration,
    totalTokens,
    cost: 0, // Cost estimation requires model-specific pricing, skip for now
    params,
    finalPassed,
    suites: suites.length > 0 ? suites : undefined,
    agentHarness: agent || undefined,
    sessionId: first.session_id ?? undefined,
    sessionEndReason: first.session_end_reason ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Trajectory summary (lightweight, for list pages)
// ---------------------------------------------------------------------------

export type TrajectorySummaryRow = {
  trajectory_id: string;
  agent_model: string;
  environment: string;
  agent: string;
  started_at: string;
  total_parts: number | bigint;
  total_turns: number | bigint;
  total_tokens: number | bigint;
  session_end_reason: string | undefined;
  task_params: string | undefined;
  suites: string | undefined;
};

export function summaryRowToTrajectory(
  row: TrajectorySummaryRow,
  finalScore?: { passed: number; failed: number; total: number },
): Trajectory {
  const suites = parseSuites(row.suites);
  const totalTests = suites.length > 0 ? computeTotalTests(suites) : 0;
  const params = parseTaskParams(row.task_params);
  const passed = finalScore?.passed ?? 0;

  const agent = row.agent ?? "";
  const agentModel = row.agent_model ?? "";
  const model = agent ? `${agent}/${agentModel}` : agentModel;

  // Build a minimal single-commit trajectory for the list view
  const commit: Commit = {
    index: 0,
    hash: "",
    turn: 0,
    timestamp: row.started_at ?? "",
    minutesElapsed: 0,
    suiteState: {},
    totalPassed: passed,
    delta: 0,
    isRegression: false,
    isMilestone: false,
    feedback: {
      passedDelta: 0,
      newlyBroken: 0,
      newlyFixed: 0,
      brokenTests: [],
      totalPassed: passed,
      totalFailed: finalScore?.failed ?? 0,
    },
    steps: [],
    changedFiles: [],
    codeSnapshot: {},
    phase: totalTests > 0 ? passed / totalTests : 0,
    tokensUsed: toNumber(row.total_tokens),
  };

  return {
    id: row.trajectory_id,
    model,
    environment: row.environment ?? "",
    commits: [commit],
    totalTests,
    startedAt: row.started_at ?? "",
    duration: "",
    totalTokens: toNumber(row.total_tokens),
    cost: 0,
    params,
    finalPassed: passed,
    suites: suites.length > 0 ? suites : undefined,
    agentHarness: agent || undefined,
    sessionEndReason: row.session_end_reason ?? undefined,
  };
}
