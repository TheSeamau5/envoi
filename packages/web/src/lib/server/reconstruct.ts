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
  CodeSnapshot,
  FileSnapshot,
} from "@/lib/types";
import { computeTotalTests } from "@/lib/constants";

// ---------------------------------------------------------------------------
// Raw Parquet row type (subset of fields we actually use)
// ---------------------------------------------------------------------------

export type ParquetRow = {
  trajectory_id: string;
  session_id?: string;
  agent?: string;
  agent_model?: string;
  started_at?: string;
  environment?: string;
  task_params?: string;
  part: number;
  timestamp?: string;
  role?: string;
  part_type?: string;
  item_type?: string;
  summary?: string;
  duration_ms?: number | bigint;
  git_commit?: string;
  content?: string;
  content_token_estimate?: number;
  tool_name?: string;
  tool_status?: string;
  tool_input?: string;
  tool_output?: string;
  tool_error?: string;
  tool_exit_code?: number;
  token_usage?: string;
  patch?: string;
  repo_checkpoint?: string;
  testing_state?: string;
  eval_events_delta?: string;
  turn?: number;
  session_end_reason?: string;
  session_end_total_parts?: number;
  session_end_total_turns?: number;
  session_end_final_commit?: string;
  suites?: string;
  files?: string;
  bundle_uri?: string;
  sandbox_id?: string;
  sandbox_provider?: string;
};

// ---------------------------------------------------------------------------
// JSON parsing helpers
// ---------------------------------------------------------------------------

function parseJson(value: string | undefined): unknown {
  if (!value) {
    return undefined;
  }
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
  if (val === undefined) {
    return 0;
  }
  return typeof val === "bigint" ? Number(val) : val;
}

// ---------------------------------------------------------------------------
// Evaluation reconstruction (port of Python build_evaluations_from_parts)
// ---------------------------------------------------------------------------

type EvalRecord = {
  evalId: string;
  commit: string;
  part: number;
  triggerTurn?: number;
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
    if (!Array.isArray(events)) {
      continue;
    }

    for (const event of events) {
      if (typeof event !== "object" || event === null) {
        continue;
      }
      if (event.kind !== "commit_async") {
        continue;
      }
      const commit = event.target_commit;
      if (typeof commit !== "string" || !commit) {
        continue;
      }

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
      if (!rec) {
        continue;
      }

      if (typeof event.status === "string" && event.status) {
        rec.status = event.status;
      }
      if (typeof event.eval_id === "string") {
        rec.evalId = event.eval_id;
      }
      if (typeof event.trigger_turn === "number") {
        rec.triggerTurn = event.trigger_turn;
      }
      if (typeof event.passed === "number") {
        rec.passed = event.passed;
      }
      if (typeof event.failed === "number") {
        rec.failed = event.failed;
      }
      if (typeof event.total === "number") {
        rec.total = event.total;
      }
      if (typeof event.suite_results === "object" && event.suite_results) {
        rec.suiteResults = event.suite_results;
      }
      if (Array.isArray(event.tests)) {
        rec.tests = event.tests;
      }
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
  if (partType === "reasoning") {
    return "reasoning";
  }
  if (partType === "text") {
    return "text";
  }

  if (partType === "function_call" || partType === "tool_call") {
    const name = (toolName ?? "").toLowerCase();
    if (name.includes("read") || name.includes("cat")) {
      return "file_read";
    }
    if (
      name.includes("write") ||
      name.includes("edit") ||
      name.includes("create") ||
      name.includes("patch")
    ) {
      return "file_write";
    }
    if (
      name.includes("test") ||
      name.includes("envoi") ||
      name.includes("eval")
    ) {
      return "test_run";
    }
    if (name.includes("mcp") || name.includes("server")) {
      return "mcp_call";
    }
    return "tool_call";
  }

  return "tool_call";
}

/**
 * Deduplicate reasoning content where the ingestion pipeline doubles lines.
 * Pattern: "**A**\n**A**" → "**A**", or "**A**\n**B**\n**A**\n**B**" → "**A**\n**B**"
 */
function deduplicateLines(text: string): string {
  if (!text) {
    return text;
  }
  const lines = text.split("\n");
  const lineCount = lines.length;
  if (lineCount < 2 || lineCount % 2 !== 0) {
    return text;
  }
  const half = lineCount / 2;
  for (let index = 0; index < half; index++) {
    if (lines[index] !== lines[half + index]) {
      return text;
    }
  }
  return lines.slice(0, half).join("\n");
}

/**
 * Deduplicate reasoning summary where the ingestion pipeline concatenates
 * the same text with a space: "**A** **A**" → "**A**"
 */
function deduplicateSummary(text: string): string {
  if (!text || text.length < 3) {
    return text;
  }
  const midpoint = Math.floor(text.length / 2);
  const searchRange = Math.min(10, Math.floor(text.length / 4));
  for (let offset = 0; offset <= searchRange; offset++) {
    const positions = offset === 0
      ? [midpoint]
      : [midpoint - offset, midpoint + offset];
    for (const splitPos of positions) {
      if (splitPos > 0 && splitPos < text.length - 1 && text[splitPos] === " ") {
        if (text.slice(0, splitPos) === text.slice(splitPos + 1)) {
          return text.slice(0, splitPos);
        }
      }
    }
  }
  return text;
}

function rowToStep(row: ParquetRow, index: number): Step {
  const stepType = mapPartType(row.part_type, row.item_type, row.tool_name);

  let summary = row.summary ?? "";
  let detail = row.content ?? "";
  let reasoningContent: string | undefined =
    row.part_type === "reasoning" ? row.content ?? undefined : undefined;

  // Deduplicate reasoning text (ingestion pipeline sometimes doubles content)
  if (row.part_type === "reasoning") {
    detail = deduplicateLines(detail);
    summary = deduplicateSummary(summary);

    // Handle structured reasoning JSON in both detail and summary fields
    // The ingestion pipeline may store {"type":"reasoning","summary":[],"content":[]}
    // in either field.
    for (const source of [detail, summary]) {
      const parsed = parseJson(source);
      if (!isRecord(parsed) || parsed.type !== "reasoning") {
        continue;
      }

      const rawContent = parsed.content;
      if (Array.isArray(rawContent)) {
        detail = rawContent
          .filter((item): item is string => typeof item === "string")
          .join("\n");
      } else if (typeof rawContent === "string") {
        detail = rawContent;
      } else {
        detail = "";
      }

      const rawSummary = parsed.summary;
      if (Array.isArray(rawSummary)) {
        const joined = rawSummary
          .filter((item): item is string => typeof item === "string")
          .join("\n");
        summary = joined;
      } else if (typeof rawSummary === "string") {
        summary = rawSummary;
      } else {
        summary = "";
      }
      break;
    }

    // Treat literal "[]" summary as empty
    if (summary === "[]") {
      summary = "";
    }

    reasoningContent = detail || undefined;
  }

  return {
    type: stepType,
    summary,
    detail,
    index,
    durationMs: toNumber(row.duration_ms) || undefined,
    tokensUsed: row.content_token_estimate ?? undefined,
    toolInput: row.tool_input ?? undefined,
    toolOutput: row.tool_output ?? undefined,
    isError:
      row.tool_status === "error" ||
      (row.tool_exit_code !== undefined &&
        row.tool_exit_code !== 0),
    errorMessage: row.tool_error ?? undefined,
    reasoningContent,
  };
}

/** Check whether a step has no meaningful content to display */
function isEmptyStep(step: Step): boolean {
  if (step.type !== "reasoning") {
    return false;
  }
  return (
    step.summary.length === 0 &&
    step.detail.length === 0 &&
    step.reasoningContent === undefined
  );
}

// ---------------------------------------------------------------------------
// Changed files from repo_checkpoint
// ---------------------------------------------------------------------------

function extractChangedFiles(
  checkpoint: unknown,
): ChangedFile[] {
  if (!isRecord(checkpoint)) {
    return [];
  }
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
          if (typeof entry.path === "string") {
            numstatMap.set(entry.path, {
              additions: typeof entry.additions === "number" ? entry.additions : 0,
              deletions: typeof entry.deletions === "number" ? entry.deletions : 0,
            });
          }
        }
      }
    }
    return files
      .filter((item): item is string => typeof item === "string")
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

/**
 * Extract the suite name from a hierarchical key like "all/basics/smoke" → "basics".
 * Falls back to the full key if it doesn't match the expected pattern.
 */
function extractSuiteName(key: string): string {
  const parts = key.split("/");
  // Pattern: "all/<suite>/<subtest>" — return the suite segment
  if (parts.length >= 2) {
    return parts[1] ?? key;
  }
  return key;
}

/**
 * Narrow an unknown record into the expected suite_results shape.
 * Each value is validated to have numeric passed/total fields.
 */
function narrowSuiteResults(
  raw: Record<string, unknown>,
): Record<string, { passed: number; total: number }> {
  const result: Record<string, { passed: number; total: number }> = {};
  for (const [key, val] of Object.entries(raw)) {
    if (isRecord(val)) {
      result[key] = {
        passed: typeof val.passed === "number" ? val.passed : 0,
        total: typeof val.total === "number" ? val.total : 0,
      };
    }
  }
  return result;
}

function buildSuiteState(
  suiteResults: Record<string, { passed: number; total: number }>,
): SuiteState {
  const state: SuiteState = {};
  for (const [key, result] of Object.entries(suiteResults)) {
    const suiteName = extractSuiteName(key);
    const passed =
      typeof result === "object" && result !== null
        ? typeof result.passed === "number"
          ? result.passed
          : 0
        : typeof result === "number"
          ? result
          : 0;
    state[suiteName] = (state[suiteName] ?? 0) + passed;
  }
  return state;
}

/**
 * Diff two suite states to find per-suite regressions.
 * Returns one BrokenTest entry per suite that lost tests.
 */
function buildBrokenTests(
  prevSuiteState: SuiteState,
  currentSuiteState: SuiteState,
): { suite: string; testId: string; error: string }[] {
  const broken: { suite: string; testId: string; error: string }[] = [];
  const allSuites = new Set([
    ...Object.keys(prevSuiteState),
    ...Object.keys(currentSuiteState),
  ]);
  for (const suite of allSuites) {
    const prev = prevSuiteState[suite] ?? 0;
    const curr = currentSuiteState[suite] ?? 0;
    const lost = prev - curr;
    if (lost > 0) {
      broken.push({
        suite,
        testId: `${lost} test${lost > 1 ? "s" : ""} regressed`,
        error: `${prev} → ${curr} passed`,
      });
    }
  }
  return broken;
}

/**
 * Compute per-suite broken and fixed counts from suite state diffs.
 * A suite that lost tests contributes to newlyBroken even if the overall
 * total went up (masked by gains in other suites).
 */
function computeSuiteDeltas(
  prevSuiteState: SuiteState,
  currentSuiteState: SuiteState,
): { newlyBroken: number; newlyFixed: number } {
  let broken = 0;
  let fixed = 0;
  const allSuites = new Set([
    ...Object.keys(prevSuiteState),
    ...Object.keys(currentSuiteState),
  ]);
  for (const suite of allSuites) {
    const prev = prevSuiteState[suite] ?? 0;
    const curr = currentSuiteState[suite] ?? 0;
    if (curr < prev) {
      broken += prev - curr;
    } else if (curr > prev) {
      fixed += curr - prev;
    }
  }
  return { newlyBroken: broken, newlyFixed: fixed };
}

// ---------------------------------------------------------------------------
// Filter empty commits and recompute deltas
// ---------------------------------------------------------------------------

/**
 * Filter out commits with no steps and recompute sequential deltas.
 * Empty commits represent re-evaluations of unchanged code; any score
 * differences are eval noise, not agent progress.
 */
function filterEmptyCommits(commits: Commit[]): Commit[] {
  const filtered = commits.filter((commit) => commit.steps.length > 0);
  if (filtered.length === 0) {
    return commits;
  }

  let prevTotalPassed = 0;
  let prevSuiteState: SuiteState = {};

  for (let commitIdx = 0; commitIdx < filtered.length; commitIdx++) {
    const commit = filtered[commitIdx];
    if (!commit) {
      continue;
    }

    commit.index = commitIdx;
    const delta = commit.totalPassed - prevTotalPassed;
    const suiteDeltas = computeSuiteDeltas(prevSuiteState, commit.suiteState);
    commit.delta = delta;
    commit.isRegression = suiteDeltas.newlyBroken > 0;
    commit.feedback = {
      ...commit.feedback,
      passedDelta: delta,
      newlyBroken: suiteDeltas.newlyBroken,
      newlyFixed: suiteDeltas.newlyFixed,
      brokenTests: buildBrokenTests(prevSuiteState, commit.suiteState),
    };

    prevTotalPassed = commit.totalPassed;
    prevSuiteState = commit.suiteState;
  }

  return filtered;
}

// ---------------------------------------------------------------------------
// Parse suites from the Parquet data
// ---------------------------------------------------------------------------

export function parseSuites(suitesJson: string | undefined): Suite[] {
  const parsed = parseJson(suitesJson);
  if (parsed === undefined || parsed === null) {
    return [];
  }

  // suites can be { "suiteName": { "total": N, ... }, ... } or an array
  if (Array.isArray(parsed)) {
    return parsed
      .filter(
        (element): element is { name: string; total: number } =>
          typeof element === "object" &&
          element !== null &&
          typeof element.name === "string" &&
          typeof element.total === "number",
      )
      .map((suite) => ({ name: suite.name, total: suite.total }));
  }

  // Object form: { "all/basics/smoke": { total: 7, ... }, ... }
  // Aggregate by suite name (second path segment)
  if (!isRecord(parsed)) {
    return [];
  }
  const aggregated = new Map<string, number>();
  for (const [key, val] of Object.entries(parsed)) {
    if (typeof val !== "object" || val === null) {
      continue;
    }
    const suiteName = extractSuiteName(key);
    const record = isRecord(val) ? val : {};
    const total = typeof record.total === "number" ? record.total : 0;
    aggregated.set(suiteName, (aggregated.get(suiteName) ?? 0) + total);
  }
  return [...aggregated.entries()].map(([name, total]) => ({ name, total }));
}

/**
 * Merge suite definitions from parquet with the union of all eval suite_results.
 * Older trajectories stored only the last eval's suite_results as the "suites"
 * field, so when a suite timed out in that eval it went missing. By taking the
 * union across all evals we recover the full environment definition.
 */
function mergeSuitesFromEvals(
  parquetSuites: Suite[],
  evals: { suiteResults: Record<string, { passed: number; total: number }> }[],
): Suite[] {
  // Start with a map of suite totals from the parquet definition
  const totals = new Map<string, number>();
  for (const suite of parquetSuites) {
    totals.set(suite.name, suite.total);
  }

  // Merge in suite data from every eval's suite_results
  for (const evalRec of evals) {
    const aggregated = new Map<string, number>();
    for (const [key, result] of Object.entries(evalRec.suiteResults)) {
      const suiteName = extractSuiteName(key);
      aggregated.set(suiteName, (aggregated.get(suiteName) ?? 0) + result.total);
    }
    for (const [name, total] of aggregated) {
      const existing = totals.get(name) ?? 0;
      if (total > existing) {
        totals.set(name, total);
      }
    }
  }

  if (totals.size === 0) {
    return parquetSuites;
  }

  return [...totals.entries()].map(([name, total]) => ({ name, total }));
}

/**
 * Deduplicate evals targeting the same git commit.
 * When multiple evals run on the same commit (re-evaluations, overlapping
 * async evals), keep only the most complete result — the one with the
 * highest `total` (most suites completed). This prevents phantom regressions
 * caused by suite timeouts varying between eval runs.
 */
function deduplicateEvalsByCommit(evals: EvalRecord[]): EvalRecord[] {
  const bestByCommit = new Map<string, EvalRecord>();
  for (const evalRec of evals) {
    const existing = bestByCommit.get(evalRec.commit);
    if (!existing || evalRec.total > existing.total) {
      bestByCommit.set(evalRec.commit, evalRec);
    }
  }
  // Preserve the original chronological order (sorted by part)
  return evals.filter(
    (evalRec) => bestByCommit.get(evalRec.commit) === evalRec,
  );
}

// ---------------------------------------------------------------------------
// Parse task_params
// ---------------------------------------------------------------------------

function parseTaskParams(
  taskParamsJson: string | undefined,
): Record<string, string> {
  const parsed = parseJson(taskParamsJson);
  if (!isRecord(parsed)) {
    return {};
  }
  const result: Record<string, string> = {};
  for (const [key, val] of Object.entries(parsed)) {
    result[key] = String(val ?? "");
  }
  return result;
}

// ---------------------------------------------------------------------------
// Unified diff parsing + code snapshot construction
// ---------------------------------------------------------------------------

/** A single file's diff within a unified patch */
type FileDiff = {
  path: string;
  isNew: boolean;
  isDeleted: boolean;
  hunks: DiffHunk[];
};

/** A hunk within a unified diff */
type DiffHunk = {
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: string[];
};

/** Normalize file paths: strip leading /workspace/ or a/ b/ prefixes */
function normalizePath(rawPath: string): string {
  let cleaned = rawPath;
  if (cleaned.startsWith("a/") || cleaned.startsWith("b/")) {
    cleaned = cleaned.slice(2);
  }
  if (cleaned.startsWith("/workspace/")) {
    cleaned = cleaned.slice("/workspace/".length);
  }
  return cleaned;
}

/** Parse a unified diff string into per-file diffs */
function parseUnifiedDiff(patchText: string): FileDiff[] {
  const fileDiffs: FileDiff[] = [];
  const fileSections = patchText.split(/^diff --git /m);

  for (const section of fileSections) {
    if (!section.trim()) {
      continue;
    }

    const lines = section.split("\n");
    const headerLine = lines[0] ?? "";

    /** Extract path from "a/path b/path" header */
    const headerMatch = headerLine.match(/^a\/(.+?) b\/(.+)$/);
    if (!headerMatch) {
      continue;
    }
    const filePath = normalizePath(headerMatch[2] ?? "");
    if (!filePath) {
      continue;
    }

    let isNew = false;
    let isDeleted = false;
    const hunks: DiffHunk[] = [];
    let lineIdx = 1;

    /** Skip metadata lines until we hit --- or a hunk header */
    while (lineIdx < lines.length) {
      const line = lines[lineIdx] ?? "";
      if (line.startsWith("new file")) {
        isNew = true;
      }
      if (line.startsWith("deleted file")) {
        isDeleted = true;
      }
      if (line.startsWith("---") || line.startsWith("@@")) {
        break;
      }
      lineIdx++;
    }

    /** Skip --- and +++ lines */
    if (lineIdx < lines.length && (lines[lineIdx] ?? "").startsWith("---")) {
      lineIdx++;
    }
    if (lineIdx < lines.length && (lines[lineIdx] ?? "").startsWith("+++")) {
      lineIdx++;
    }

    /** Parse hunks */
    while (lineIdx < lines.length) {
      const line = lines[lineIdx] ?? "";
      const hunkMatch = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
      if (!hunkMatch) {
        lineIdx++;
        continue;
      }

      const hunk: DiffHunk = {
        oldStart: parseInt(hunkMatch[1] ?? "1", 10),
        oldCount: parseInt(hunkMatch[2] ?? "1", 10),
        newStart: parseInt(hunkMatch[3] ?? "1", 10),
        newCount: parseInt(hunkMatch[4] ?? "1", 10),
        lines: [],
      };
      lineIdx++;

      /** Collect hunk body lines */
      while (lineIdx < lines.length) {
        const hunkLine = lines[lineIdx] ?? "";
        if (
          hunkLine.startsWith("+") ||
          hunkLine.startsWith("-") ||
          hunkLine.startsWith(" ")
        ) {
          hunk.lines.push(hunkLine);
          lineIdx++;
        } else if (hunkLine.startsWith("\\")) {
          /** "\ No newline at end of file" — skip */
          lineIdx++;
        } else {
          break;
        }
      }

      hunks.push(hunk);
    }

    /** Skip binary/mode-only changes with no hunks */
    if (hunks.length > 0) {
      fileDiffs.push({ path: filePath, isNew, isDeleted, hunks });
    }
  }

  return fileDiffs;
}

/**
 * Apply a single file diff to the virtual filesystem.
 * Returns the set of 0-based line indices that were added.
 */
function applyFileDiff(
  fileDiffs: FileDiff,
  fileSystem: Record<string, string[]>,
): { addedLines: number[] } {
  const addedLines: number[] = [];

  if (fileDiffs.isDeleted) {
    delete fileSystem[fileDiffs.path];
    return { addedLines };
  }

  if (fileDiffs.isNew) {
    /** New file — all "+" lines form the content */
    const content: string[] = [];
    for (const hunk of fileDiffs.hunks) {
      for (const line of hunk.lines) {
        if (line.startsWith("+")) {
          addedLines.push(content.length);
          content.push(line.slice(1));
        }
      }
    }
    fileSystem[fileDiffs.path] = content;
    return { addedLines };
  }

  /** Modified file — apply hunks to existing content */
  const oldLines = fileSystem[fileDiffs.path] ?? [];
  const newLines: string[] = [];
  let oldCursor = 0;

  for (const hunk of fileDiffs.hunks) {
    /** Copy unchanged lines before this hunk */
    const hunkOldStart = hunk.oldStart - 1;
    while (oldCursor < hunkOldStart && oldCursor < oldLines.length) {
      newLines.push(oldLines[oldCursor] ?? "");
      oldCursor++;
    }

    /** Apply hunk lines */
    for (const line of hunk.lines) {
      if (line.startsWith("+")) {
        addedLines.push(newLines.length);
        newLines.push(line.slice(1));
      } else if (line.startsWith("-")) {
        oldCursor++;
      } else if (line.startsWith(" ")) {
        newLines.push(line.slice(1));
        oldCursor++;
      }
    }
  }

  /** Copy remaining lines after last hunk */
  while (oldCursor < oldLines.length) {
    newLines.push(oldLines[oldCursor] ?? "");
    oldCursor++;
  }

  fileSystem[fileDiffs.path] = newLines;
  return { addedLines };
}

/**
 * Build code snapshots for each commit by applying patches sequentially.
 * Mutates commit.codeSnapshot in place.
 */
function populateCodeSnapshots(
  sortedRows: ParquetRow[],
  commits: Commit[],
): void {
  /** Virtual filesystem: path → lines */
  const fileSystem: Record<string, string[]> = {};
  let rowCursor = 0;

  for (const commit of commits) {
    const addedInCommit: Record<string, number[]> = {};
    const touchedInCommit = new Set<string>();
    const newInCommit = new Set<string>();

    /** Process rows belonging to this commit (one row per step) */
    const rowCount = commit.steps.length;
    const endCursor = rowCursor + rowCount;

    while (rowCursor < endCursor && rowCursor < sortedRows.length) {
      const row = sortedRows[rowCursor];
      if (row?.patch && row.patch.length > 0) {
        const fileDiffs = parseUnifiedDiff(row.patch);
        for (const diff of fileDiffs) {
          const { addedLines } = applyFileDiff(diff, fileSystem);
          touchedInCommit.add(diff.path);
          if (diff.isNew) {
            newInCommit.add(diff.path);
          }
          if (addedLines.length > 0) {
            const existing = addedInCommit[diff.path];
            if (existing) {
              existing.push(...addedLines);
            } else {
              addedInCommit[diff.path] = [...addedLines];
            }
          }
        }
      }
      rowCursor++;
    }

    /** Build CodeSnapshot from current filesystem state */
    const snapshot: CodeSnapshot = {};
    for (const [filePath, lines] of Object.entries(fileSystem)) {
      const added = addedInCommit[filePath] ?? [];
      const fileSnapshot: FileSnapshot = {
        lines: [...lines],
        added: added.sort((lineA, lineB) => lineA - lineB),
        touched: touchedInCommit.has(filePath),
        isNew: newInCommit.has(filePath) ? true : undefined,
      };
      snapshot[filePath] = fileSnapshot;
    }

    commit.codeSnapshot = snapshot;
  }
}

// ---------------------------------------------------------------------------
// Main reconstruction: rows → Trajectory
// ---------------------------------------------------------------------------

export function reconstructTrajectory(rows: ParquetRow[]): Trajectory {
  const first = rows[0];
  if (!first) {
    throw new Error("Cannot reconstruct trajectory from empty rows");
  }
  const sortedRows = [...rows].sort((rowA, rowB) => rowA.part - rowB.part);

  // Parse trajectory-level fields
  const params = parseTaskParams(first.task_params);

  // Build evaluations
  const evalMap = buildEvaluationsFromRows(sortedRows);
  const completedEvals = [...evalMap.values()]
    .filter((rec) => rec.status === "completed" && rec.total > 0)
    .sort((recA, recB) => recA.part - recB.part);

  // Compute suites: merge the parquet-level definition with the union of all
  // eval suite_results. Older trajectories may have partial suites in the
  // parquet (only the last eval's results). Merging across all evals ensures
  // we recover the full environment definition.
  const suites = mergeSuitesFromEvals(parseSuites(first.suites), completedEvals);
  const totalTests = suites.length > 0 ? computeTotalTests(suites) : 0;

  // Deduplicate evals on the same git commit: keep only the most complete
  // result (highest total). Multiple evals can target the same commit when
  // the agent re-evaluates without changing code, or when async evals
  // overlap. Without dedup, phantom regressions appear when a suite times
  // out in one eval but not another.
  const deduped = deduplicateEvalsByCommit(completedEvals);

  // Build commits from evaluation boundaries
  const commits: Commit[] = [];
  let prevTotalPassed = 0;
  let prevSuiteState: SuiteState = {};

  if (deduped.length === 0) {
    // No evaluations — create a single "commit" containing all steps
    const steps = sortedRows
      .map((row, rowIndex) => rowToStep(row, rowIndex))
      .filter((step) => !isEmptyStep(step))
      .map((step, filteredIndex) => ({ ...step, index: filteredIndex }));
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
        totalFailed: totalTests,
      },
      steps,
      changedFiles: extractChangedFiles(lastCheckpoint),
      codeSnapshot: {},
      phase: 0,
      tokensUsed: steps.reduce((sum, step) => sum + (step.tokensUsed ?? 0), 0),
      partRange: sortedRows.length > 0
        ? [sortedRows[0]?.part ?? 0, lastSortedRow?.part ?? 0]
        : undefined,
    });
  } else {
    // Group rows by evaluation boundaries
    let rowCursor = 0;

    for (const evalRec of deduped) {
      // Collect rows from cursor up to and including the eval trigger part
      const commitRows: ParquetRow[] = [];
      while (rowCursor < sortedRows.length) {
        const currentRow = sortedRows[rowCursor];
        if (!currentRow) {
          break;
        }
        commitRows.push(currentRow);
        rowCursor++;
        // If we've passed the trigger part, stop
        if (currentRow.part >= evalRec.part) {
          break;
        }
      }

      const steps = commitRows
        .map((row, rowIndex) => rowToStep(row, rowIndex))
        .filter((step) => !isEmptyStep(step))
        .map((step, filteredIndex) => ({ ...step, index: filteredIndex }));
      const suiteState = buildSuiteState(evalRec.suiteResults);
      const delta = evalRec.passed - prevTotalPassed;
      const suiteDeltas = computeSuiteDeltas(prevSuiteState, suiteState);
      const brokenTests = buildBrokenTests(prevSuiteState, suiteState);

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
        isRegression: suiteDeltas.newlyBroken > 0,
        isMilestone: false,
        feedback: {
          passedDelta: delta,
          newlyBroken: suiteDeltas.newlyBroken,
          newlyFixed: suiteDeltas.newlyFixed,
          brokenTests,
          totalPassed: evalRec.passed,
          totalFailed: evalRec.failed,
        },
        steps,
        changedFiles: extractChangedFiles(checkpoint),
        codeSnapshot: {},
        phase: totalTests > 0 ? evalRec.passed / totalTests : 0,
        tokensUsed: steps.reduce((sum, step) => sum + (step.tokensUsed ?? 0), 0),
        partRange: commitRows.length > 0
          ? [commitRows[0]?.part ?? 0, commitRows[commitRows.length - 1]?.part ?? 0]
          : undefined,
        evalId: evalRec.evalId,
        targetCommit: evalRec.commit,
      });

      prevTotalPassed = evalRec.passed;
      prevSuiteState = suiteState;
    }

    // Remaining rows after the last evaluation
    if (rowCursor < sortedRows.length) {
      const remainingRows = sortedRows.slice(rowCursor);
      const steps = remainingRows
        .map((row, rowIndex) => rowToStep(row, rowIndex))
        .filter((step) => !isEmptyStep(step))
        .map((step, filteredIndex) => ({ ...step, index: filteredIndex }));
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
          totalFailed: totalTests - prevTotalPassed,
        },
        steps,
        changedFiles: extractChangedFiles(lastCheckpoint),
        codeSnapshot: {},
        phase: totalTests > 0 ? prevTotalPassed / totalTests : 0,
        tokensUsed: steps.reduce((sum, step) => sum + (step.tokensUsed ?? 0), 0),
        partRange: remainingRows.length > 0
          ? [remainingRows[0]?.part ?? 0, remainingRows[remainingRows.length - 1]?.part ?? 0]
          : undefined,
      });
    }
  }

  // Build code snapshots from accumulated patch diffs
  populateCodeSnapshots(sortedRows, commits);

  // Filter out empty commits (no steps) and recompute deltas.
  // Empty commits represent re-evaluations of unchanged code; any score
  // differences are eval noise, not agent progress.
  const filteredCommits = filterEmptyCommits(commits);

  // Compute duration
  const lastCommit = filteredCommits[filteredCommits.length - 1];
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
  const lastEval = deduped[deduped.length - 1];
  const finalPassed = lastEval ? lastEval.passed : 0;

  // Model string
  const agent = first.agent ?? "";
  const agentModel = first.agent_model ?? "";
  const model = agent ? `${agent}/${agentModel}` : agentModel;

  return {
    id: first.trajectory_id,
    model,
    environment: deriveEnvironment(first.environment, suites, first.task_params),
    commits: filteredCommits,
    totalTests,
    startedAt: first.started_at ?? "",
    duration,
    totalTokens,
    cost: 0,
    params,
    finalPassed,
    suites: suites.length > 0 ? suites : undefined,
    agentHarness: agent || undefined,
    sessionId: first.session_id ?? undefined,
    sessionEndReason: first.session_end_reason ?? undefined,
    sandboxId: first.sandbox_id ?? undefined,
    sandboxProvider: first.sandbox_provider ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Environment derivation
// ---------------------------------------------------------------------------

/** Gameboy emulator suite names */
const GAMEBOY_SUITES = new Set([
  "acid2_dmg", "acid2_cgb",
  "blargg_cpu", "blargg_timing", "blargg_sound", "blargg_misc",
  "mealybug_dmg", "mealybug_cgb",
  "mooneye_timer", "mooneye_mbc", "mooneye_acceptance",
  "samesuite",
]);

/** C compiler suite names */
const C_COMPILER_SUITES = new Set(["basics", "wacct", "c_testsuite", "torture"]);

/**
 * Derive the real environment from available data.
 * The parquet `environment` column is often a placeholder ("environment"),
 * so we use suite names and task_params to determine the actual environment.
 */
export function deriveEnvironment(
  rawEnvironment: string | undefined,
  suites: Suite[],
  taskParamsJson?: string,
): string {
  /** 1. If the raw environment is a real value (not a placeholder), use it */
  if (rawEnvironment && rawEnvironment !== "environment" && rawEnvironment !== "") {
    return rawEnvironment;
  }

  /** 2. Derive from suite names */
  for (const suite of suites) {
    if (GAMEBOY_SUITES.has(suite.name)) {
      return "gameboy_emulator";
    }
    if (C_COMPILER_SUITES.has(suite.name)) {
      return "c_compiler";
    }
  }

  /** 3. Derive from task_params advisor_system_prompt */
  if (taskParamsJson) {
    try {
      const params = JSON.parse(taskParamsJson);
      const advisorPrompt = params?._environment_params?.advisor_system_prompt ?? "";
      if (typeof advisorPrompt === "string") {
        if (advisorPrompt.includes("Game Boy") || advisorPrompt.includes("emulator")) {
          return "gameboy_emulator";
        }
        if (advisorPrompt.includes("compiler") || advisorPrompt.includes("C compiler")) {
          return "c_compiler";
        }
      }
      /** 4. Check for failed_tests_feedback_limit (c_compiler specific param) */
      if (params?._environment_params?.failed_tests_feedback_limit) {
        return "c_compiler";
      }
    } catch {
      /* ignore parse errors */
    }
  }

  return rawEnvironment || "unknown";
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
  session_end_reason?: string;
  task_params?: string;
  suites?: string;
};

export function summaryRowToTrajectory(
  row: TrajectorySummaryRow,
  finalScore?: { passed: number; failed: number; total: number },
  endedAt?: string,
  evalCount?: number,
): Trajectory {
  const suites = parseSuites(row.suites);
  const passed = finalScore?.passed ?? 0;
  // Safeguard: totalTests must be at least as large as the passed count.
  // Old trajectories may have partial suite data from incomplete evals.
  const totalTests = Math.max(
    suites.length > 0 ? computeTotalTests(suites) : 0,
    finalScore?.total ?? 0,
    passed,
  );
  const params = parseTaskParams(row.task_params);

  const agent = row.agent ?? "";
  const agentModel = row.agent_model ?? "";
  const model = agent ? `${agent}/${agentModel}` : agentModel;
  const environment = deriveEnvironment(row.environment, suites, row.task_params);

  /** Compute duration from started_at → ended_at timestamps */
  let durationMinutes = 0;
  let durationStr = "";
  const startMs = new Date(row.started_at ?? "").getTime();
  const endMs = new Date(endedAt ?? "").getTime();
  if (!isNaN(startMs) && !isNaN(endMs) && endMs > startMs) {
    durationMinutes = Math.round((endMs - startMs) / 60_000);
    const hours = Math.floor(durationMinutes / 60);
    const mins = durationMinutes % 60;
    durationStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  }

  /** Build a minimal single-commit trajectory for the list view */
  const commit: Commit = {
    index: 0,
    hash: "",
    turn: 0,
    timestamp: row.started_at ?? "",
    minutesElapsed: durationMinutes,
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
    /** Store eval count in evalId for the list view to read */
    evalId: evalCount !== undefined ? String(evalCount) : undefined,
  };

  return {
    id: row.trajectory_id,
    model,
    environment,
    commits: [commit],
    totalTests,
    startedAt: row.started_at ?? "",
    duration: durationStr,
    totalTokens: toNumber(row.total_tokens),
    cost: 0,
    params,
    finalPassed: passed,
    suites: suites.length > 0 ? suites : undefined,
    agentHarness: agent || undefined,
    sessionEndReason: row.session_end_reason ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Summary table row → Trajectory (for materialized summary tables)
// ---------------------------------------------------------------------------

/**
 * Row shape from the materialized trajectory_summary table.
 * Unlike TrajectorySummaryRow, this already contains final_passed/failed/total.
 */
export type SummaryTableRow = {
  trajectory_id: string;
  environment: string;
  agent: string;
  agent_model: string;
  started_at: string;
  ended_at: string;
  total_parts: number | bigint;
  total_turns: number | bigint;
  total_tokens: number | bigint;
  session_end_reason?: string;
  task_params?: string;
  suites?: string;
  final_passed: number;
  final_failed: number;
  final_total: number;
  final_suite_results?: string;
  bundle_uri?: string;
};

/**
 * Convert a materialized summary table row into a Trajectory.
 * No second query needed — final scores are pre-computed.
 */
export function summaryTableRowToTrajectory(
  row: SummaryTableRow,
): Trajectory {
  const suites = parseSuites(row.suites);
  const params = parseTaskParams(row.task_params);
  const passed = row.final_passed ?? 0;
  const failed = row.final_failed ?? 0;
  // Safeguard: totalTests must be at least as large as passed count.
  // Old trajectories may have partial suite data from incomplete evals.
  const totalTests = Math.max(
    suites.length > 0 ? computeTotalTests(suites) : 0,
    row.final_total ?? 0,
    passed,
  );


  const agent = row.agent ?? "";
  const agentModel = row.agent_model ?? "";
  const model = agent ? `${agent}/${agentModel}` : agentModel;
  const environment = deriveEnvironment(row.environment, suites, row.task_params);

  // Compute duration from started_at and ended_at
  const startMs = new Date(row.started_at ?? "").getTime();
  const endMs = new Date(row.ended_at ?? "").getTime();
  let duration = "";
  if (!isNaN(startMs) && !isNaN(endMs)) {
    const totalMinutes = Math.round((endMs - startMs) / 60_000);
    const hours = Math.floor(totalMinutes / 60);
    const mins = totalMinutes % 60;
    duration = hours > 0 ? `${hours} hrs ${mins} min` : `${mins} min`;
  }

  // Parse final suite results for suite state
  const finalSuiteResults = parseJson(row.final_suite_results);
  const suiteState = isRecord(finalSuiteResults)
    ? buildSuiteState(narrowSuiteResults(finalSuiteResults))
    : {};

  const commit: Commit = {
    index: 0,
    hash: "",
    turn: 0,
    timestamp: row.started_at ?? "",
    minutesElapsed: 0,
    suiteState,
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
      totalFailed: failed,
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
    environment,
    commits: [commit],
    totalTests,
    startedAt: row.started_at ?? "",
    duration,
    totalTokens: toNumber(row.total_tokens),
    cost: 0,
    params,
    finalPassed: passed,
    suites: suites.length > 0 ? suites : undefined,
    agentHarness: agent || undefined,
    sessionEndReason: row.session_end_reason ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Evaluation summary row → used by compare trajectories
// ---------------------------------------------------------------------------

/** Row shape from the materialized evaluation_summary table */
export type EvalSummaryRow = {
  trajectory_id: string;
  environment: string;
  agent_model: string;
  eval_id: string;
  target_commit: string;
  trigger_part: number;
  trigger_turn?: number;
  status: string;
  passed: number;
  failed: number;
  total: number;
  suite_results?: string;
  queued_at?: string;
  started_at?: string;
  finished_at?: string;
};

/**
 * Build Trajectory objects for the compare page from summary + evaluation rows.
 * Each evaluation becomes a commit with totalPassed, delta, suiteState, etc.
 * Steps are empty since the compare page only needs curves, not step detail.
 */
/**
 * Deduplicate compare eval rows targeting the same git commit.
 * Keeps the eval with the highest total (most suites completed).
 */
function deduplicateCompareEvalsByCommit(evals: EvalSummaryRow[]): EvalSummaryRow[] {
  const bestByCommit = new Map<string, EvalSummaryRow>();
  for (const evalRow of evals) {
    const existing = bestByCommit.get(evalRow.target_commit);
    if (!existing || evalRow.total > existing.total) {
      bestByCommit.set(evalRow.target_commit, evalRow);
    }
  }
  return evals.filter(
    (evalRow) => bestByCommit.get(evalRow.target_commit) === evalRow,
  );
}

export function buildCompareTrajectories(
  summaryRows: Record<string, unknown>[],
  evalRows: Record<string, unknown>[],
): Trajectory[] {
  // Group eval rows by trajectory
  const evalsByTrajectory = new Map<string, EvalSummaryRow[]>();
  for (const raw of evalRows) {
    const trajectoryId = String(raw.trajectory_id ?? "");
    const evalRow: EvalSummaryRow = {
      trajectory_id: trajectoryId,
      environment: String(raw.environment ?? ""),
      agent_model: String(raw.agent_model ?? ""),
      eval_id: String(raw.eval_id ?? ""),
      target_commit: String(raw.target_commit ?? ""),
      trigger_part: Number(raw.trigger_part ?? 0),
      trigger_turn: raw.trigger_turn != undefined ? Number(raw.trigger_turn) : undefined,
      status: String(raw.status ?? ""),
      passed: Number(raw.passed ?? 0),
      failed: Number(raw.failed ?? 0),
      total: Number(raw.total ?? 0),
      suite_results: raw.suite_results != undefined ? String(raw.suite_results) : undefined,
    };
    const existing = evalsByTrajectory.get(trajectoryId);
    if (existing) {
      existing.push(evalRow);
    } else {
      evalsByTrajectory.set(trajectoryId, [evalRow]);
    }
  }

  const trajectories: Trajectory[] = [];
  for (const raw of summaryRows) {
    const tableRow = toSummaryTableRow(raw);
    const suites = parseSuites(tableRow.suites);
    const totalTests = suites.length > 0 ? computeTotalTests(suites) : 0;
    const params = parseTaskParams(tableRow.task_params);

    const agent = tableRow.agent ?? "";
    const agentModel = tableRow.agent_model ?? "";
    const model = agent ? `${agent}/${agentModel}` : agentModel;

    const rawEvals = evalsByTrajectory.get(tableRow.trajectory_id) ?? [];
    rawEvals.sort((evalA, evalB) => evalA.trigger_part - evalB.trigger_part);
    const evals = deduplicateCompareEvalsByCommit(rawEvals);

    // Merge suite_results from all evals to recover full suite definitions
    const evalRecordsForMerge = evals
      .filter((evalRow) => evalRow.suite_results)
      .map((evalRow) => {
        const parsed = parseJson(evalRow.suite_results);
        return {
          suiteResults: isRecord(parsed) ? narrowSuiteResults(parsed) : {},
        };
      });
    const mergedSuites = mergeSuitesFromEvals(suites, evalRecordsForMerge);
    const mergedTotalTests = mergedSuites.length > 0 ? computeTotalTests(mergedSuites) : totalTests;

    const commits: Commit[] = [];
    let prevPassed = 0;
    let prevTableSuiteState: SuiteState = {};

    for (const evalRow of evals) {
      const suiteResultsParsed = parseJson(evalRow.suite_results);
      const suiteState = isRecord(suiteResultsParsed)
        ? buildSuiteState(narrowSuiteResults(suiteResultsParsed))
        : {};
      const delta = evalRow.passed - prevPassed;
      const suiteDeltas = computeSuiteDeltas(prevTableSuiteState, suiteState);
      const brokenTests = buildBrokenTests(prevTableSuiteState, suiteState);

      commits.push({
        index: commits.length,
        hash: evalRow.target_commit,
        turn: evalRow.trigger_turn ?? commits.length,
        timestamp: "",
        minutesElapsed: 0,
        suiteState,
        totalPassed: evalRow.passed,
        delta,
        isRegression: suiteDeltas.newlyBroken > 0,
        isMilestone: false,
        feedback: {
          passedDelta: delta,
          newlyBroken: suiteDeltas.newlyBroken,
          newlyFixed: suiteDeltas.newlyFixed,
          brokenTests,
          totalPassed: evalRow.passed,
          totalFailed: evalRow.failed,
        },
        steps: [],
        changedFiles: [],
        codeSnapshot: {},
        phase: mergedTotalTests > 0 ? evalRow.passed / mergedTotalTests : 0,
        tokensUsed: 0,
        evalId: evalRow.eval_id,
        targetCommit: evalRow.target_commit,
      });

      prevPassed = evalRow.passed;
      prevTableSuiteState = suiteState;
    }

    // If no evaluations, create a single empty commit
    if (commits.length === 0) {
      commits.push({
        index: 0,
        hash: "",
        turn: 0,
        timestamp: tableRow.started_at ?? "",
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
          totalFailed: mergedTotalTests,
        },
        steps: [],
        changedFiles: [],
        codeSnapshot: {},
        phase: 0,
        tokensUsed: 0,
      });
    }

    trajectories.push({
      id: tableRow.trajectory_id,
      model,
      environment: tableRow.environment ?? "",
      commits,
      totalTests: mergedTotalTests,
      startedAt: tableRow.started_at ?? "",
      duration: "",
      totalTokens: toNumber(tableRow.total_tokens),
      cost: 0,
      params,
      finalPassed: tableRow.final_passed ?? 0,
      suites: mergedSuites.length > 0 ? mergedSuites : undefined,
      agentHarness: agent || undefined,
      sessionEndReason: tableRow.session_end_reason ?? undefined,
    });
  }

  return trajectories;
}

/** Validate a raw query row into a SummaryTableRow */
export function toSummaryTableRow(row: Record<string, unknown>): SummaryTableRow {
  return {
    trajectory_id: String(row.trajectory_id ?? ""),
    environment: String(row.environment ?? ""),
    agent: String(row.agent ?? ""),
    agent_model: String(row.agent_model ?? ""),
    started_at: String(row.started_at ?? ""),
    ended_at: String(row.ended_at ?? ""),
    total_parts: Number(row.total_parts ?? 0),
    total_turns: Number(row.total_turns ?? 0),
    total_tokens: Number(row.total_tokens ?? 0),
    session_end_reason: row.session_end_reason != undefined ? String(row.session_end_reason) : undefined,
    task_params: row.task_params != undefined ? String(row.task_params) : undefined,
    suites: row.suites != undefined ? String(row.suites) : undefined,
    final_passed: Number(row.final_passed ?? 0),
    final_failed: Number(row.final_failed ?? 0),
    final_total: Number(row.final_total ?? 0),
    final_suite_results: row.final_suite_results != undefined ? String(row.final_suite_results) : undefined,
    bundle_uri: row.bundle_uri != undefined ? String(row.bundle_uri) : undefined,
  };
}
