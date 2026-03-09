/**
 * Server-side data access layer.
 *
 * Queries DuckDB/S3 for real data, falls back to mock when S3 is not configured.
 * Uses materialized summary tables when available for fast list queries.
 * Derives environment from suite names when the environment column is a placeholder.
 * Import this module only from server components or API route handlers.
 */

import { access, stat } from "node:fs/promises";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import {
  isS3Configured,
  traceUri,
  freshTraceUri,
  codeSnapshotsUri,
  logsUri,
  freshLogsUri,
  query,
} from "./db";
import { freshnessFromBool, readProjectData } from "./project-data";
import { sqlLiteral } from "./utils";
import {
  reconstructTrajectory,
  summaryRowToTrajectory,
  parseSuites,
  type ParquetRow,
  type TrajectorySummaryRow,
} from "./reconstruct";
import type {
  Trajectory,
  Suite,
  CodeSnapshot,
  FileSnapshot,
  DifficultyCell,
  PortfolioRow,
  PortfolioEnvironmentRow,
  ParetoPoint,
  SchemaColumn,
  TrajectoryLogRow,
} from "@/lib/types";

const execFileAsync = promisify(execFile);
const EXTRACT_EVAL_ROWS_SCRIPT = path.join(
  process.cwd(),
  "scripts",
  "extract_eval_rows.py",
);
const REPO_PYTHON = path.resolve(
  process.cwd(),
  "..",
  "..",
  ".venv",
  "bin",
  "python3",
);

// ---------------------------------------------------------------------------
// Mock fallback (lazy import to avoid bundling when not needed)
// ---------------------------------------------------------------------------

/** Load all mock trajectories (lazy import) */
async function getMockTrajectories(): Promise<Trajectory[]> {
  const { generateAllTrajectories } = await import("@/lib/mock");
  return generateAllTrajectories();
}

/** Load a single mock trajectory by ID (lazy import) */
async function getMockTrajectoryById(
  id: string,
): Promise<Trajectory | undefined> {
  const { getTrajectoryById } = await import("@/lib/mock");
  return getTrajectoryById(id);
}

// ---------------------------------------------------------------------------
// Row validation helpers
// ---------------------------------------------------------------------------

/** Validate a raw query row into a TrajectorySummaryRow */
function toSummaryRow(row: Record<string, unknown>): TrajectorySummaryRow {
  return {
    trajectory_id: String(row.trajectory_id ?? ""),
    agent_model: String(row.agent_model ?? ""),
    environment: String(row.environment ?? ""),
    agent: String(row.agent ?? ""),
    started_at: String(row.started_at ?? ""),
    total_parts: Number(row.total_parts ?? 0),
    total_turns: Number(row.total_turns ?? 0),
    total_tokens: Number(row.total_tokens ?? 0),
    session_end_reason:
      row.session_end_reason != undefined
        ? String(row.session_end_reason)
        : undefined,
    sandbox_id:
      row.sandbox_id != undefined ? String(row.sandbox_id) : undefined,
    sandbox_provider:
      row.sandbox_provider != undefined
        ? String(row.sandbox_provider)
        : undefined,
    task_params:
      row.task_params != undefined ? String(row.task_params) : undefined,
    suites: row.suites != undefined ? String(row.suites) : undefined,
  };
}

/** Validate a raw query row into a ParquetRow */
function toParquetRow(row: Record<string, unknown>): ParquetRow {
  return {
    trajectory_id: String(row.trajectory_id ?? ""),
    session_id:
      row.session_id != undefined ? String(row.session_id) : undefined,
    agent: row.agent != undefined ? String(row.agent) : undefined,
    agent_model:
      row.agent_model != undefined ? String(row.agent_model) : undefined,
    started_at:
      row.started_at != undefined ? String(row.started_at) : undefined,
    environment:
      row.environment != undefined ? String(row.environment) : undefined,
    task_params:
      row.task_params != undefined ? String(row.task_params) : undefined,
    part: Number(row.part ?? 0),
    timestamp: row.timestamp != undefined ? String(row.timestamp) : undefined,
    role: row.role != undefined ? String(row.role) : undefined,
    part_type: row.part_type != undefined ? String(row.part_type) : undefined,
    item_type: row.item_type != undefined ? String(row.item_type) : undefined,
    summary: row.summary != undefined ? String(row.summary) : undefined,
    duration_ms:
      row.duration_ms != undefined ? Number(row.duration_ms) : undefined,
    git_commit:
      row.git_commit != undefined ? String(row.git_commit) : undefined,
    content: row.content != undefined ? String(row.content) : undefined,
    content_token_estimate:
      row.content_token_estimate != undefined
        ? Number(row.content_token_estimate)
        : undefined,
    tool_name: row.tool_name != undefined ? String(row.tool_name) : undefined,
    tool_status:
      row.tool_status != undefined ? String(row.tool_status) : undefined,
    tool_input:
      row.tool_input != undefined ? String(row.tool_input) : undefined,
    tool_output:
      row.tool_output != undefined ? String(row.tool_output) : undefined,
    tool_error:
      row.tool_error != undefined ? String(row.tool_error) : undefined,
    tool_exit_code:
      row.tool_exit_code != undefined ? Number(row.tool_exit_code) : undefined,
    token_usage:
      row.token_usage != undefined
        ? typeof row.token_usage === "string"
          ? row.token_usage
          : JSON.stringify(row.token_usage)
        : undefined,
    patch: row.patch != undefined ? String(row.patch) : undefined,
    repo_checkpoint:
      row.repo_checkpoint != undefined
        ? String(row.repo_checkpoint)
        : undefined,
    testing_state:
      row.testing_state != undefined
        ? typeof row.testing_state === "string"
          ? row.testing_state
          : JSON.stringify(row.testing_state)
        : undefined,
    eval_events_delta:
      row.eval_events_delta != undefined
        ? typeof row.eval_events_delta === "string"
          ? row.eval_events_delta
          : JSON.stringify(row.eval_events_delta)
        : undefined,
    turn: row.turn != undefined ? Number(row.turn) : undefined,
    session_end_reason:
      row.session_end_reason != undefined
        ? String(row.session_end_reason)
        : undefined,
    session_end_total_parts:
      row.session_end_total_parts != undefined
        ? Number(row.session_end_total_parts)
        : undefined,
    session_end_total_turns:
      row.session_end_total_turns != undefined
        ? Number(row.session_end_total_turns)
        : undefined,
    session_end_final_commit:
      row.session_end_final_commit != undefined
        ? String(row.session_end_final_commit)
        : undefined,
    suites: row.suites != undefined ? String(row.suites) : undefined,
    files: row.files != undefined ? String(row.files) : undefined,
    bundle_uri:
      row.bundle_uri != undefined ? String(row.bundle_uri) : undefined,
    sandbox_id:
      row.sandbox_id != undefined ? String(row.sandbox_id) : undefined,
    sandbox_provider:
      row.sandbox_provider != undefined
        ? String(row.sandbox_provider)
        : undefined,
  };
}

type TrajectoryScore = {
  passed: number;
  failed: number;
  total: number;
};

const RAW_CACHE_DETAIL_LIMIT_BYTES = 100 * 1024 * 1024;

async function resolvePythonExecutable(): Promise<string> {
  try {
    await access(REPO_PYTHON);
    return REPO_PYTHON;
  } catch {
    return "python3";
  }
}

function deriveScoreFromSuites(
  suitesValue: string | undefined,
): TrajectoryScore | undefined {
  if (!suitesValue) {
    return undefined;
  }
  try {
    const parsed: unknown = JSON.parse(suitesValue);
    if (typeof parsed !== "object" || parsed === null) {
      return undefined;
    }
    let passed = 0;
    let total = 0;
    for (const value of Object.values(parsed)) {
      if (typeof value !== "object" || value === null) {
        continue;
      }
      const rowPassed =
        "passed" in value && typeof value.passed === "number"
          ? value.passed
          : 0;
      const rowTotal =
        "total" in value && typeof value.total === "number" ? value.total : 0;
      passed += rowPassed;
      total += rowTotal;
    }
    if (total <= 0) {
      return undefined;
    }
    return {
      passed,
      failed: Math.max(0, total - passed),
      total,
    };
  } catch {
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Get all trajectory summaries (lightweight, for list pages).
 * Uses materialized summary tables when available (fast path),
 * otherwise falls back to a GROUP BY scan of raw parquet files (slow path).
 * Optionally filter by environment or model.
 */
export async function getAllTrajectories(opts?: {
  environment?: string;
  model?: string;
  limit?: number;
  offset?: number;
  fresh?: boolean;
  project?: string;
}): Promise<Trajectory[]> {
  if (!isS3Configured()) {
    return getMockTrajectories();
  }

  return readProjectData({
    project: opts?.project,
    freshness: freshnessFromBool(opts?.fresh),
    cacheKey: `all-trajectories:${opts?.project ?? ""}:${opts?.environment ?? ""}:${opts?.model ?? ""}:${opts?.limit ?? ""}:${opts?.offset ?? ""}`,
    load: async (project) => {
      let sql = `
        SELECT
          *
        FROM trajectories s
        WHERE 1=1
      `;

      if (opts?.environment) {
        sql += ` AND s.environment = '${sqlLiteral(opts.environment)}'`;
      }
      if (opts?.model) {
        sql += ` AND s.agent_model = '${sqlLiteral(opts.model)}'`;
      }

      sql += ` ORDER BY s.started_at DESC`;

      if (opts?.limit) {
        sql += ` LIMIT ${Number(opts.limit)}`;
      }
      if (opts?.offset) {
        sql += ` OFFSET ${Number(opts.offset)}`;
      }

      const rawRows = await query(sql, project);
      return rawRows.map((row) => {
        const summaryRow = toSummaryRow(row);
        const finalScore =
          row.best_passed !== undefined && row.best_passed !== null
            ? {
                passed: Number(row.best_passed ?? 0),
                failed: Number(row.best_failed ?? 0),
                total: Number(row.best_total ?? 0),
              }
            : deriveScoreFromSuites(summaryRow.suites);

        const trajectory = summaryRowToTrajectory(
          summaryRow,
          finalScore,
          String(row.ended_at ?? ""),
          Number(row.eval_count ?? 0),
        );
        trajectory.totalParts = Number(row.total_parts ?? 0);
        trajectory.sessionEndReason =
          summaryRow.session_end_reason ?? undefined;
        return trajectory;
      });
    },
  });
}

/**
 * Get a single trajectory with full detail (all commits, steps, evaluations).
 */
export async function getTrajectoryById(
  id: string,
  options?: { fresh?: boolean; project?: string },
): Promise<Trajectory | undefined> {
  if (!isS3Configured()) {
    return getMockTrajectoryById(id);
  }
  return readProjectData({
    project: options?.project,
    freshness: freshnessFromBool(options?.fresh),
    cacheKey: `trajectory:${options?.project ?? "default"}:${id}`,
    load: (project) => loadTrajectory(id, options?.fresh === true, project),
  });
}

/**
 * Reconstruct a trajectory directly from the locally cached raw trace parquet.
 * This keeps full `eval_events_delta` available without forcing an S3 read.
 */
export async function getTrajectoryByIdFromRawCache(
  id: string,
  project?: string,
): Promise<Trajectory | undefined> {
  if (!isS3Configured()) {
    return getMockTrajectoryById(id);
  }
  try {
    const uri = await traceUri(id, project);
    if (!uri.startsWith("s3://")) {
      const info = await stat(uri);
      if (info.size > RAW_CACHE_DETAIL_LIMIT_BYTES) {
        return undefined;
      }
    }
    return loadTrajectoryFromRawUri(uri, project);
  } catch (error) {
    console.error(
      `[data] loadTrajectoryFromRawCache failed id=${id}:`,
      error instanceof Error ? error.message : error,
    );
    return undefined;
  }
}

/**
 * Build a serving-grade trajectory detail from raw cached parquet plus
 * externally extracted eval rows, avoiding DuckDB's large eval JSON reads.
 */
export async function getTrajectoryByIdForServing(
  id: string,
  project?: string,
): Promise<Trajectory | undefined> {
  if (!isS3Configured()) {
    return getMockTrajectoryById(id);
  }

  try {
    const uri = await traceUri(id, project);
    const rawRows = await query(
      `
      SELECT * EXCLUDE (eval_events_delta)
      FROM read_parquet('${sqlLiteral(uri)}')
      ORDER BY part
    `,
      project,
    );
    if (rawRows.length === 0) {
      return undefined;
    }

    if (uri.startsWith("s3://")) {
      return reconstructTrajectory(rawRows.map(toParquetRow));
    }

    const evalRows = await extractEvalRowsFromTrace(uri);
    if (evalRows.length === 0) {
      return reconstructTrajectory(rawRows.map(toParquetRow));
    }

    const evalsByPart = groupEvalsByPart(evalRows);
    return reconstructTrajectory(injectEvalData(rawRows, evalsByPart));
  } catch (error) {
    console.error(
      `[data] loadTrajectoryForServing failed id=${id}:`,
      error instanceof Error ? error.message : error,
    );
    return undefined;
  }
}

/**
 * Load a single trajectory from parquet + materialized evaluations.
 * Excludes eval_events_delta from the parquet read (176MB savings on large
 * files) and injects evaluation data from the materialized evaluations table.
 */
/** Group materialized eval rows by part and return a map of synthetic eval events. */
function groupEvalsByPart(
  evalRows: Record<string, unknown>[],
): Map<number, Record<string, unknown>[]> {
  const evalsByPart = new Map<number, Record<string, unknown>[]>();
  for (const evalRow of evalRows) {
    const part = Number(evalRow.part ?? 0);
    let bucket = evalsByPart.get(part);
    if (!bucket) {
      bucket = [];
      evalsByPart.set(part, bucket);
    }
    bucket.push({
      kind: "commit_async",
      eval_id: String(evalRow.eval_id ?? ""),
      target_commit: String(evalRow.target_commit ?? ""),
      trigger_part: part,
      trigger_turn:
        evalRow.turn != undefined ? Number(evalRow.turn) : undefined,
      status: String(evalRow.status ?? ""),
      passed: Number(evalRow.passed ?? 0),
      failed: Number(evalRow.failed ?? 0),
      total: Number(evalRow.total ?? 0),
      suite_results: (() => {
        try {
          return JSON.parse(String(evalRow.suite_results ?? "{}"));
        } catch {
          return {};
        }
      })(),
      finished_at:
        evalRow.finished_at != undefined
          ? String(evalRow.finished_at)
          : undefined,
    });
  }
  return evalsByPart;
}

/** Inject materialized eval data into parquet rows, replacing any raw eval_events_delta. */
function injectEvalData(
  rawRows: Record<string, unknown>[],
  evalsByPart: Map<number, Record<string, unknown>[]>,
): ParquetRow[] {
  return rawRows.map((raw) => {
    const row = toParquetRow(raw);
    const events = evalsByPart.get(row.part);
    if (events) {
      row.eval_events_delta = JSON.stringify(events);
    }
    return row;
  });
}

async function extractEvalRowsFromTrace(
  tracePath: string,
): Promise<Record<string, unknown>[]> {
  const pythonExecutable = await resolvePythonExecutable();
  const { stdout } = await execFileAsync(pythonExecutable, [
    EXTRACT_EVAL_ROWS_SCRIPT,
    tracePath,
  ]);
  const parsed: unknown = JSON.parse(stdout);
  if (!Array.isArray(parsed)) {
    return [];
  }
  return parsed.filter(
    (row): row is Record<string, unknown> =>
      typeof row === "object" && row !== null && !Array.isArray(row),
  );
}

async function loadTrajectoryFromRawUri(
  uri: string,
  project?: string,
): Promise<Trajectory | undefined> {
  const rawRows = await query(
    `
    SELECT *
    FROM read_parquet('${sqlLiteral(uri)}')
    ORDER BY part
  `,
    project,
  );

  if (rawRows.length === 0) {
    return undefined;
  }

  return reconstructTrajectory(rawRows.map(toParquetRow));
}

async function loadTrajectory(
  id: string,
  fresh: boolean,
  project?: string,
): Promise<Trajectory | undefined> {
  try {
    const uri = fresh
      ? await freshTraceUri(id, project)
      : await traceUri(id, project);

    if (fresh) {
      return loadTrajectoryFromRawUri(uri, project);
    }

    // Cached read: exclude eval_events_delta (5MB vs 181MB for large files)
    // and use the materialized evaluations table instead.
    const [rawRows, evalRows] = await Promise.all([
      query(
        `
        SELECT * EXCLUDE (eval_events_delta)
        FROM read_parquet('${sqlLiteral(uri)}')
        ORDER BY part
      `,
        project,
      ),
      query(
        `
        SELECT part, turn, eval_id, status, passed, failed, total,
          target_commit, suite_results, finished_at
        FROM evaluations
        WHERE trajectory_id = '${sqlLiteral(id)}'
        ORDER BY part
      `,
        project,
      ),
    ]);

    if (rawRows.length === 0) {
      return undefined;
    }

    // If the evaluations table has data, inject it into the rows.
    if (evalRows.length > 0) {
      const evalsByPart = groupEvalsByPart(evalRows);
      return reconstructTrajectory(injectEvalData(rawRows, evalsByPart));
    }

    // Evaluations table empty — reconstruct without eval data.
    // Do NOT fall back to SELECT * which includes eval_events_delta
    // (100-300MB) and OOMs within the 960MB DuckDB limit.
    return reconstructTrajectory(rawRows.map(toParquetRow));
  } catch (error) {
    console.error(
      `[data] loadTrajectory failed id=${id} fresh=${fresh}:`,
      error instanceof Error ? error.message : error,
    );
    return undefined;
  }
}

/**
 * Lightweight fetch of sandbox metadata for a trajectory.
 * Only reads 1 row with 3 columns — much faster than getTrajectoryById.
 */
export async function getTrajectorySandboxMeta(
  id: string,
  project?: string,
): Promise<
  | { sessionEndReason?: string; sandboxId?: string; sandboxProvider?: string }
  | undefined
> {
  if (!isS3Configured()) {
    return undefined;
  }
  return readProjectData({
    project,
    freshness: "cached",
    cacheKey: `trajectory-sandbox-meta:${project ?? "default"}:${id}`,
    load: async (activeProject) => {
      try {
        const summaryRows = await query(
          `
          SELECT session_end_reason, sandbox_id, sandbox_provider
          FROM trajectories
          WHERE trajectory_id = '${sqlLiteral(id)}'
          LIMIT 1
        `,
          activeProject,
        );
        if (summaryRows.length === 0) {
          return undefined;
        }
        const row = summaryRows[0];
        if (!row) {
          return undefined;
        }
        return {
          sessionEndReason:
            row.session_end_reason != undefined
              ? String(row.session_end_reason)
              : undefined,
          sandboxId:
            row.sandbox_id != undefined ? String(row.sandbox_id) : undefined,
          sandboxProvider:
            row.sandbox_provider != undefined
              ? String(row.sandbox_provider)
              : undefined,
        };
      } catch {
        return undefined;
      }
    },
  });
}

/**
 * Get evaluations for a trajectory (for progress curves).
 */
export async function getTrajectoryEvaluations(
  id: string,
  project?: string,
): Promise<
  {
    evalId: string;
    part: number;
    passed: number;
    failed: number;
    total: number;
    suiteResults: Record<string, { passed: number; total: number }>;
    targetCommit: string;
  }[]
> {
  if (!isS3Configured()) {
    const traj = await getMockTrajectoryById(id);
    if (!traj) {
      return [];
    }
    return traj.commits.map((commit, commitIndex) => ({
      evalId: `mock-eval-${commitIndex}`,
      part: commitIndex,
      passed: commit.totalPassed,
      failed: commit.feedback.totalFailed,
      total: commit.totalPassed + commit.feedback.totalFailed,
      suiteResults: Object.fromEntries(
        Object.entries(commit.suiteState).map(([key, val]) => [
          key,
          { passed: val, total: 0 },
        ]),
      ),
      targetCommit: commit.hash,
    }));
  }
  return readProjectData({
    project,
    freshness: "cached",
    cacheKey: `trajectory-evaluations:${project ?? "default"}:${id}`,
    load: async (activeProject) => {
      try {
        const sql = `
          SELECT eval_id, part, passed, failed, total, suite_results, target_commit
          FROM evaluations
          WHERE trajectory_id = '${sqlLiteral(id)}'
            AND status = 'completed'
          ORDER BY part
        `;
        const rawRows = await query(sql, activeProject);

        return rawRows.map((row) => ({
          evalId: String(row.eval_id ?? ""),
          part: Number(row.part ?? 0),
          passed: Number(row.passed ?? 0),
          failed: Number(row.failed ?? 0),
          total: Number(row.total ?? 0),
          suiteResults: (() => {
            try {
              const raw = row.suite_results;
              return JSON.parse(typeof raw === "string" ? raw : "{}");
            } catch {
              return {};
            }
          })(),
          targetCommit: String(row.target_commit ?? ""),
        }));
      } catch {
        return [];
      }
    },
  });
}

/**
 * Get distinct environments from the data.
 */
export async function getEnvironments(
  project?: string,
  options?: { fresh?: boolean },
): Promise<
  {
    environment: string;
    suites: Suite[];
    trajectoryCount: number;
    modelCount: number;
  }[]
> {
  if (!isS3Configured()) {
    return [
      {
        environment: "c_compiler",
        suites: (await import("@/lib/constants")).SUITES,
        trajectoryCount: 30,
        modelCount: 5,
      },
    ];
  }

  return readProjectData({
    project,
    freshness: freshnessFromBool(options?.fresh),
    cacheKey: `environments:${project ?? "default"}`,
    load: async (activeProject) => {
      const sql = `
        SELECT
          environment,
          MIN(suites) AS suites,
          COUNT(*) AS trajectory_count,
          COUNT(DISTINCT agent_model) AS model_count
        FROM trajectories
        GROUP BY environment
        ORDER BY environment
      `;
      const rawRows = await query(sql, activeProject);
      return rawRows.map((row) => ({
        environment: String(row.environment ?? ""),
        suites: parseSuites(
          row.suites !== undefined ? String(row.suites) : undefined,
        ),
        trajectoryCount: Number(row.trajectory_count ?? 0),
        modelCount: Number(row.model_count ?? 0),
      }));
    },
  });
}

/**
 * Get trajectories for comparison with full commit histories.
 * Can filter by specific IDs or environment.
 *
 * Unlike getAllTrajectories() which returns lightweight summaries,
 * this returns trajectories with commit data needed for progress curves.
 * Uses materialized summary + evaluation tables when available.
 */
export async function getCompareTrajectories(opts?: {
  ids?: string[];
  environment?: string;
  fresh?: boolean;
  project?: string;
}): Promise<Trajectory[]> {
  if (!isS3Configured()) {
    const all = await getMockTrajectories();
    if (opts?.ids && opts.ids.length > 0) {
      const idSet = new Set(opts.ids);
      return all.filter((traj) => idSet.has(traj.id));
    }
    return all;
  }

  return readProjectData({
    project: opts?.project,
    freshness: freshnessFromBool(opts?.fresh),
    cacheKey: `compare-trajectories:${opts?.project ?? "default"}:${(opts?.ids ?? []).join(",")}:${opts?.environment ?? ""}`,
    load: async (project) => {
      let ids = opts?.ids ?? [];

      if (ids.length === 0) {
        let sql = `SELECT trajectory_id FROM trajectories WHERE 1=1`;
        if (opts?.environment) {
          sql += ` AND environment = '${sqlLiteral(opts.environment)}'`;
        }
        sql += ` ORDER BY started_at DESC`;
        const rows = await query(sql, project);
        ids = rows.map((row) => String(row.trajectory_id ?? ""));
      }

      const results = await Promise.all(
        ids.map((id) => loadTrajectory(id, opts?.fresh === true, project)),
      );

      return results.filter((traj): traj is Trajectory => traj !== undefined);
    },
  });
}

// ---------------------------------------------------------------------------
// Code history (Layer 5)
// ---------------------------------------------------------------------------

/** Raw row from code_snapshots.parquet */
type CodeSnapshotRow = {
  commit_hash: string;
  commit_index: number;
  file_path: string;
  status: string;
  content: string;
  added_lines: string;
};

/** Validate a raw query row into a CodeSnapshotRow */
function toCodeSnapshotRow(row: Record<string, unknown>): CodeSnapshotRow {
  return {
    commit_hash: String(row.commit_hash ?? ""),
    commit_index: Number(row.commit_index ?? 0),
    file_path: String(row.file_path ?? ""),
    status: String(row.status ?? ""),
    content: String(row.content ?? ""),
    added_lines: String(row.added_lines ?? "[]"),
  };
}

/** Parse added_lines JSON string into number[] */
function parseAddedLines(jsonStr: string): number[] {
  try {
    const parsed: unknown = JSON.parse(jsonStr);
    if (Array.isArray(parsed)) {
      return parsed.filter((item): item is number => typeof item === "number");
    }
    return [];
  } catch {
    return [];
  }
}

/**
 * Get code history for a trajectory — one CodeSnapshot per commit index.
 * Returns a map from commit index to CodeSnapshot.
 * Returns undefined if code_snapshots.parquet does not exist for this trajectory.
 */
export async function getCodeHistory(
  trajectoryId: string,
  project?: string,
): Promise<Record<number, CodeSnapshot> | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }
  return readProjectData({
    project,
    freshness: "cached",
    cacheKey: `trajectory-code-history:${project ?? "default"}:${trajectoryId}`,
    load: async (activeProject) => {
      const uri = await codeSnapshotsUri(trajectoryId, activeProject);
      if (uri === undefined) {
        return undefined;
      }

      try {
        const sql = `
          SELECT commit_hash, commit_index, file_path, status, content, added_lines
          FROM read_parquet('${sqlLiteral(uri)}')
          ORDER BY commit_index, file_path
        `;
        const rawRows = await query(sql, activeProject);

        const result: Record<number, CodeSnapshot> = {};

        for (const raw of rawRows) {
          const row = toCodeSnapshotRow(raw);
          if (result[row.commit_index] === undefined) {
            result[row.commit_index] = {};
          }

          const fileSnapshot: FileSnapshot = {
            lines: row.content.split("\n"),
            added: parseAddedLines(row.added_lines),
            touched: row.status === "A" || row.status === "M",
            isNew: row.status === "A" ? true : undefined,
          };

          const target = result[row.commit_index];
          if (target !== undefined) {
            target[row.file_path] = fileSnapshot;
          }
        }

        return result;
      } catch (error) {
        console.error(
          `[data] Failed to load code history for ${trajectoryId}:`,
          error,
        );
        return undefined;
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Structured logs (logs.parquet)
// ---------------------------------------------------------------------------

/** Raw row from logs.parquet */
type TrajectoryLogRowRaw = {
  seq: number;
  ts: string;
  component: string;
  event: string;
  level: string;
  message: string;
  turn?: number;
  part?: number;
  git_commit?: string;
  session_id?: string;
  source?: string;
  fields?: string;
};

/** Validate a raw query row into a TrajectoryLogRowRaw */
function toTrajectoryLogRowRaw(
  row: Record<string, unknown>,
): TrajectoryLogRowRaw {
  return {
    seq: Number(row.seq ?? 0),
    ts: String(row.ts ?? ""),
    component: String(row.component ?? ""),
    event: String(row.event ?? ""),
    level: String(row.level ?? ""),
    message: String(row.message ?? ""),
    turn: row.turn != undefined ? Number(row.turn) : undefined,
    part: row.part != undefined ? Number(row.part) : undefined,
    git_commit:
      row.git_commit != undefined ? String(row.git_commit) : undefined,
    session_id:
      row.session_id != undefined ? String(row.session_id) : undefined,
    source: row.source != undefined ? String(row.source) : undefined,
    fields: row.fields != undefined ? String(row.fields) : undefined,
  };
}

function mapTrajectoryLogRow(raw: TrajectoryLogRowRaw): TrajectoryLogRow {
  return {
    seq: raw.seq,
    ts: raw.ts,
    component: raw.component,
    event: raw.event,
    level: raw.level,
    message: raw.message,
    turn: raw.turn,
    part: raw.part,
    gitCommit: raw.git_commit,
    sessionId: raw.session_id,
    source: raw.source,
    fields: raw.fields,
  };
}

/**
 * Get structured logs for a trajectory from logs.parquet.
 * Returns undefined when logs.parquet is unavailable.
 */
export async function getTrajectoryLogsById(
  id: string,
  opts?: {
    fresh?: boolean;
    project?: string;
    limit?: number;
    fromSeq?: number;
  },
): Promise<TrajectoryLogRow[] | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }
  const limitRaw = Number.isFinite(opts?.limit) ? Number(opts?.limit) : 2500;
  const limit = Math.max(1, Math.min(10_000, Math.floor(limitRaw)));
  const fromSeqRaw = Number.isFinite(opts?.fromSeq) ? Number(opts?.fromSeq) : 0;
  const fromSeq = Math.max(0, Math.floor(fromSeqRaw));
  return readProjectData({
    project: opts?.project,
    freshness: freshnessFromBool(opts?.fresh),
    cacheKey: `trajectory-logs:${opts?.project ?? "default"}:${id}:${fromSeq}:${limit}`,
    load: async (project) => {
      try {
        const uri =
          opts?.fresh === true
            ? await freshLogsUri(id, project)
            : await logsUri(id, project);
        const sql = `
          SELECT
            seq,
            ts,
            component,
            event,
            level,
            message,
            turn,
            part,
            git_commit,
            session_id,
            source,
            fields
          FROM read_parquet('${sqlLiteral(uri)}')
          WHERE seq > ${fromSeq}
          ORDER BY seq
          LIMIT ${limit}
        `;
        const rawRows = await query(sql, project);
        if (rawRows.length === 0) {
          return [] as TrajectoryLogRow[];
        }
        return rawRows.map((row) =>
          mapTrajectoryLogRow(toTrajectoryLogRowRaw(row)),
        );
      } catch {
        return undefined;
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Schema info (for SQL Console)
// ---------------------------------------------------------------------------

/**
 * Get database schema info — table/view names with their columns.
 * Used by the SQL Console to show a schema reference sidebar.
 */
export async function getSchemaInfo(
  project?: string,
  options?: { fresh?: boolean },
): Promise<SchemaColumn[]> {
  if (!isS3Configured()) {
    return [];
  }
  return readProjectData({
    project,
    freshness: freshnessFromBool(options?.fresh),
    cacheKey: `schema:${project ?? "default"}`,
    load: async (activeProject) => {
      try {
        const rawRows = await query(
          `
          SELECT table_name, column_name, data_type
          FROM information_schema.columns
          WHERE table_schema = 'main'
          ORDER BY table_name, ordinal_position
        `,
          activeProject,
        );

        return rawRows.map((row) => ({
          tableName: String(row.table_name ?? ""),
          columnName: String(row.column_name ?? ""),
          dataType: String(row.data_type ?? ""),
        }));
      } catch {
        return [];
      }
    },
  });
}

/**
 * Execute a read-only SQL query from the SQL Console.
 * Returns rows, column names, row count, and duration.
 */
export async function executeQuery(
  sql: string,
  project?: string,
): Promise<{
  rows: Record<string, unknown>[];
  columns: string[];
  rowCount: number;
  durationMs: number;
}> {
  return readProjectData({
    project,
    freshness: "fresh",
    load: async (activeProject) => {
      const start = Date.now();
      const rows = await query(sql, activeProject);
      const durationMs = Date.now() - start;
      const columns = rows.length > 0 ? Object.keys(rows[0] ?? {}) : [];
      return { rows, columns, rowCount: rows.length, durationMs };
    },
  });
}

// ---------------------------------------------------------------------------
// Difficulty heatmap
// ---------------------------------------------------------------------------

/** Known c_compiler suites — everything else is gameboy emulator */
const C_COMPILER_SUITES = new Set([
  "basics",
  "wacct",
  "c_testsuite",
  "torture",
]);

/** Derive environment name from suite name */
function suiteToEnvironment(suiteName: string): string {
  return C_COMPILER_SUITES.has(suiteName) ? "c_compiler" : "gameboy_emulator";
}

/**
 * Get difficulty data — per-(environment, suite, model) pass rates for the heatmap.
 *
 * Uses the `suites` column from raw parquet which contains the actual per-suite
 * pass/fail/total breakdown. The `environment` column in the parquet is a
 * placeholder ("environment"), so we derive the real environment from suite names.
 */
export async function getDifficultyData(
  project?: string,
  options?: { fresh?: boolean },
): Promise<DifficultyCell[]> {
  if (!isS3Configured()) {
    return getMockDifficultyData();
  }
  return readProjectData({
    project,
    freshness: freshnessFromBool(options?.fresh),
    cacheKey: `difficulty-data:${project ?? "default"}`,
    load: async (activeProject) => {
      try {
        const rawRows = await query(
          `
          WITH snapshots AS (
            SELECT trajectory_id, agent_model, suites::JSON AS sr
            FROM trajectories
            WHERE suites IS NOT NULL
              AND LENGTH(CAST(suites AS VARCHAR)) > 5
          ),
          suite_entries AS (
            SELECT
              agent_model,
              unnest(json_keys(sr)) AS suite_key,
              sr
            FROM snapshots
          ),
          parsed AS (
            SELECT
              agent_model,
              SPLIT_PART(suite_key, '/', 2) AS suite_name,
              CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.passed') AS DOUBLE) AS passed,
              CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.total') AS DOUBLE) AS total
            FROM suite_entries
          ),
          aggregated AS (
            SELECT
              suite_name AS category,
              agent_model AS model,
              SUM(passed) AS total_passed,
              SUM(total) AS total_total,
              COUNT(*) AS attempts
            FROM parsed
            WHERE suite_name != '' AND suite_name != 'all'
            GROUP BY suite_name, agent_model
          )
          SELECT
            category,
            model,
            CASE WHEN total_total > 0 THEN total_passed / total_total ELSE 0 END AS pass_rate,
            attempts
          FROM aggregated
          ORDER BY category, model
        `,
          activeProject,
        );

        return rawRows.map((row) => {
          const category = String(row.category ?? "");
          return {
            environment: suiteToEnvironment(category),
            category,
            model: String(row.model ?? ""),
            passRate: Number(row.pass_rate ?? 0),
            attempts: Number(row.attempts ?? 0),
          };
        });
      } catch {
        return getMockDifficultyData();
      }
    },
  });
}

/** Mock difficulty data for when S3 is not configured */
async function getMockDifficultyData(): Promise<DifficultyCell[]> {
  const models = ["gpt-4o", "claude-sonnet-4-20250514", "o3"];
  const envSuites: Record<string, string[]> = {
    c_compiler: ["basics", "wacct", "c_testsuite", "torture"],
    gameboy_emulator: ["mooneye_acceptance", "blargg_cpu", "acid2_cgb"],
  };
  const cells: DifficultyCell[] = [];
  for (const [environment, suites] of Object.entries(envSuites)) {
    for (const category of suites) {
      for (const model of models) {
        cells.push({
          environment,
          category,
          model,
          passRate: Math.random() * 0.8 + 0.1,
          attempts: Math.floor(Math.random() * 10) + 1,
        });
      }
    }
  }
  return cells;
}

// ---------------------------------------------------------------------------
// Portfolio dashboard
// ---------------------------------------------------------------------------

/**
 * Get portfolio data — per-model rankings across environments.
 *
 * Derives environment from suite names since the parquet `environment` column
 * is a placeholder. Uses per-trajectory overall pass rates from the latest
 * suites snapshot.
 */
export async function getPortfolioData(
  project?: string,
  options?: { fresh?: boolean },
): Promise<PortfolioRow[]> {
  if (!isS3Configured()) {
    return getMockPortfolioData();
  }
  return readProjectData({
    project,
    freshness: freshnessFromBool(options?.fresh),
    cacheKey: `portfolio-data:${project ?? "default"}`,
    load: async (activeProject) => {
      try {
        const rawRows = await query(
          `
          WITH snapshots AS (
            SELECT
              trajectory_id,
              agent_model,
              suites::JSON AS sr
            FROM trajectories
            WHERE suites IS NOT NULL
              AND LENGTH(CAST(suites AS VARCHAR)) > 5
          ),
          suite_entries AS (
            SELECT
              trajectory_id,
              agent_model,
              unnest(json_keys(sr)) AS suite_key,
              sr
            FROM snapshots
          ),
          per_trajectory AS (
            SELECT
              trajectory_id,
              agent_model,
              SPLIT_PART(MIN(suite_key), '/', 2) AS first_suite,
              SUM(CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.passed') AS DOUBLE)) AS total_passed,
              SUM(CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.total') AS DOUBLE)) AS total_total
            FROM suite_entries
            WHERE SPLIT_PART(suite_key, '/', 2) != 'all'
              AND SPLIT_PART(suite_key, '/', 2) != ''
            GROUP BY trajectory_id, agent_model
          ),
          with_env AS (
            SELECT
              agent_model,
              CASE
                WHEN first_suite IN ('basics', 'wacct', 'c_testsuite', 'torture')
                THEN 'c_compiler'
                ELSE 'gameboy_emulator'
              END AS environment,
              CASE WHEN total_total > 0 THEN total_passed / total_total ELSE 0 END AS pass_rate
            FROM per_trajectory
          ),
          scores AS (
            SELECT
              agent_model,
              environment,
              AVG(pass_rate) AS pass_rate
            FROM with_env
            GROUP BY agent_model, environment
          ),
          ranked AS (
            SELECT *,
              RANK() OVER (PARTITION BY environment ORDER BY pass_rate DESC) AS env_rank
            FROM scores
          )
          SELECT agent_model, environment, pass_rate, env_rank
          FROM ranked
          ORDER BY agent_model, environment
        `,
          activeProject,
        );

        return buildPortfolioRows(rawRows);
      } catch {
        return getMockPortfolioData();
      }
    },
  });
}

/** Build PortfolioRow[] from ranked query results */
function buildPortfolioRows(
  rawRows: Record<string, unknown>[],
): PortfolioRow[] {
  const modelMap = new Map<string, PortfolioRow>();

  for (const row of rawRows) {
    const model = String(row.agent_model ?? "");
    const environment = String(row.environment ?? "");
    const passRate = Number(row.pass_rate ?? 0);
    const rank = Number(row.env_rank ?? 0);

    let entry = modelMap.get(model);
    if (!entry) {
      entry = { model, environments: {}, avgRank: 0 };
      modelMap.set(model, entry);
    }
    entry.environments[environment] = { passRate, rank };
  }

  /** Compute average rank across environments */
  for (const entry of modelMap.values()) {
    const ranks = Object.values(entry.environments).map((env) => env.rank);
    entry.avgRank =
      ranks.length > 0
        ? ranks.reduce((sum, rank) => sum + rank, 0) / ranks.length
        : 0;
  }

  return Array.from(modelMap.values()).sort(
    (rowA, rowB) => rowA.avgRank - rowB.avgRank,
  );
}

/** Mock portfolio data */
async function getMockPortfolioData(): Promise<PortfolioRow[]> {
  const models = [
    "gpt-4o",
    "claude-sonnet-4-20250514",
    "o3",
    "gemini-2.5-pro",
    "deepseek-r1",
  ];
  const environments = ["c_compiler", "gameboy_emulator"];
  const rows: Record<string, unknown>[] = [];
  for (const [modelIndex, model] of models.entries()) {
    for (const environment of environments) {
      rows.push({
        agent_model: model,
        environment,
        pass_rate: 0.9 - modelIndex * 0.15,
        env_rank: modelIndex + 1,
      });
    }
  }
  return buildPortfolioRows(rows);
}

// ---------------------------------------------------------------------------
// Portfolio enrichment (Task 1)
// ---------------------------------------------------------------------------

/**
 * Get per-environment summary data for the enriched portfolio dashboard.
 * Returns best score, median pass rate, run count, and total tokens per environment.
 */
export async function getPortfolioEnvironmentData(
  project?: string,
  options?: { fresh?: boolean },
): Promise<PortfolioEnvironmentRow[]> {
  if (!isS3Configured()) {
    return getMockPortfolioEnvironmentData();
  }
  return readProjectData({
    project,
    freshness: freshnessFromBool(options?.fresh),
    cacheKey: `portfolio-env-data:${project ?? "default"}`,
    load: async (activeProject) => {
      try {
        const rawRows = await query(
          `
          WITH snapshots AS (
            SELECT
              trajectory_id,
              agent_model,
              environment,
              total_tokens,
              suites::JSON AS sr
            FROM trajectories
            WHERE suites IS NOT NULL
              AND LENGTH(CAST(suites AS VARCHAR)) > 5
          ),
          suite_entries AS (
            SELECT
              trajectory_id,
              agent_model,
              environment,
              total_tokens,
              unnest(json_keys(sr)) AS suite_key,
              sr
            FROM snapshots
          ),
          per_trajectory AS (
            SELECT
              trajectory_id,
              agent_model,
              environment,
              MAX(total_tokens) AS total_tokens,
              SUM(CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.passed') AS DOUBLE)) AS total_passed,
              SUM(CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.total') AS DOUBLE)) AS total_total
            FROM suite_entries
            WHERE SPLIT_PART(suite_key, '/', 2) != 'all'
              AND SPLIT_PART(suite_key, '/', 2) != ''
            GROUP BY trajectory_id, agent_model, environment
          ),
          ranked AS (
            SELECT
              *,
              ROW_NUMBER() OVER (
                PARTITION BY environment
                ORDER BY total_passed DESC, total_total DESC, agent_model ASC
              ) AS score_rank
            FROM per_trajectory
          ),
          env_summary AS (
            SELECT
              environment,
              COUNT(*) AS run_count,
              MAX(total_passed) AS max_passed,
              MAX(total_total) AS max_total,
              MEDIAN(CASE WHEN total_total > 0 THEN total_passed * 1.0 / total_total ELSE 0 END) AS median_pass_rate,
              SUM(total_tokens) AS total_tokens
            FROM per_trajectory
            GROUP BY environment
          ),
          best_model AS (
            SELECT
              environment,
              agent_model AS best_model
            FROM ranked
            WHERE score_rank = 1
          )
          SELECT env_summary.*, best_model.best_model
          FROM env_summary
          LEFT JOIN best_model USING (environment)
          ORDER BY environment
        `,
          activeProject,
        );

        return rawRows.map((row) => ({
          environment: String(row.environment ?? ""),
          bestPassed: Number(row.max_passed ?? 0),
          bestTotal: Number(row.max_total ?? 0),
          bestModel: String(row.best_model ?? ""),
          medianPassRate: Number(row.median_pass_rate ?? 0),
          runCount: Number(row.run_count ?? 0),
          totalTokens: Number(row.total_tokens ?? 0),
          perModelCounts: {},
        }));
      } catch {
        return getMockPortfolioEnvironmentData();
      }
    },
  });
}

/** Mock portfolio environment data */
function getMockPortfolioEnvironmentData(): PortfolioEnvironmentRow[] {
  return [
    {
      environment: "c_compiler",
      bestPassed: 891,
      bestTotal: 2184,
      bestModel: "claude-code/opus-4.6",
      medianPassRate: 0.28,
      runCount: 20,
      totalTokens: 45_000_000,
      perModelCounts: {
        "claude-code/opus-4.6": 8,
        "codex/gpt-5.3-codex": 6,
        "opencode/glm-5": 6,
      },
    },
    {
      environment: "gameboy_emulator",
      bestPassed: 156,
      bestTotal: 400,
      bestModel: "codex/gpt-5.3-codex",
      medianPassRate: 0.22,
      runCount: 10,
      totalTokens: 22_000_000,
      perModelCounts: {
        "claude-code/opus-4.6": 4,
        "codex/gpt-5.3-codex": 3,
        "opencode/glm-5": 3,
      },
    },
  ];
}

/**
 * Get Pareto frontier data — one point per trajectory with cost and score.
 * Optionally filter by environment.
 */
export async function getParetoData(
  environment?: string,
  project?: string,
  options?: { fresh?: boolean },
): Promise<ParetoPoint[]> {
  if (!isS3Configured()) {
    return getMockParetoData();
  }
  return readProjectData({
    project,
    freshness: freshnessFromBool(options?.fresh),
    cacheKey: `pareto-data:${project ?? "default"}:${environment ?? "all"}`,
    load: async (activeProject) => {
      try {
        let sql = `
          WITH snapshots AS (
            SELECT
              trajectory_id,
              agent_model,
              environment,
              total_tokens,
              suites::JSON AS sr
            FROM trajectories
            WHERE suites IS NOT NULL
              AND LENGTH(CAST(suites AS VARCHAR)) > 5
          ),
          suite_entries AS (
            SELECT
              trajectory_id,
              agent_model,
              environment,
              total_tokens,
              unnest(json_keys(sr)) AS suite_key,
              sr
            FROM snapshots
          ),
          per_trajectory AS (
            SELECT
              trajectory_id,
              agent_model,
              environment,
              MAX(total_tokens) AS total_tokens,
              SUM(CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.passed') AS DOUBLE)) AS total_passed,
              SUM(CAST(json_extract(sr, '$.' || '"' || suite_key || '"' || '.total') AS DOUBLE)) AS total_total
            FROM suite_entries
            WHERE SPLIT_PART(suite_key, '/', 2) != 'all'
              AND SPLIT_PART(suite_key, '/', 2) != ''
            GROUP BY trajectory_id, agent_model, environment
          )
          SELECT *
          FROM per_trajectory
          WHERE total_total > 0
        `;

        if (environment) {
          sql += ` AND environment = '${sqlLiteral(environment)}'`;
        }

        sql += ` ORDER BY total_tokens ASC`;

        const rawRows = await query(sql, activeProject);

        return rawRows.map((row) => {
          const passed = Number(row.total_passed ?? 0);
          const total = Number(row.total_total ?? 0);
          return {
            trajectoryId: String(row.trajectory_id ?? ""),
            model: String(row.agent_model ?? ""),
            environment: String(row.environment ?? ""),
            totalTokens: Number(row.total_tokens ?? 0),
            passed,
            total,
            passRate: total > 0 ? passed / total : 0,
          };
        });
      } catch {
        return getMockParetoData();
      }
    },
  });
}

/** Mock Pareto data */
function getMockParetoData(): ParetoPoint[] {
  const models = [
    "claude-code/opus-4.6",
    "codex/gpt-5.3-codex",
    "opencode/glm-5",
  ];
  const points: ParetoPoint[] = [];
  for (let pointIdx = 0; pointIdx < 20; pointIdx++) {
    const model = models[pointIdx % models.length] ?? models[0] ?? "";
    const tokens =
      500_000 + pointIdx * 200_000 + Math.round(Math.random() * 500_000);
    const passRate = Math.min(
      0.95,
      0.1 + pointIdx * 0.04 + Math.random() * 0.1,
    );
    points.push({
      trajectoryId: `mock-traj-${pointIdx}`,
      model,
      environment: "c_compiler",
      totalTokens: tokens,
      passed: Math.round(passRate * 2184),
      total: 2184,
      passRate,
    });
  }
  return points;
}
