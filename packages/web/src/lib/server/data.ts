/**
 * Server-side data access layer.
 *
 * Queries DuckDB/S3 for real data, falls back to mock when S3 is not configured.
 * Uses materialized summary tables when available for fast list queries.
 * Derives environment from suite names when the environment column is a placeholder.
 * Import this module only from server components or API route handlers.
 */

import {
  isS3Configured,
  allTracesGlob,
  traceUri,
  codeSnapshotsUri,
  query,
  hasSummaryTables,
} from "./db";
import {
  reconstructTrajectory,
  summaryRowToTrajectory,
  summaryTableRowToTrajectory,
  buildCompareTrajectories,
  parseSuites,
  toSummaryTableRow,
  type ParquetRow,
  type TrajectorySummaryRow,
} from "./reconstruct";
import { cached } from "./cache";
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
} from "@/lib/types";

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
// Escape helpers (prevent SQL injection for identifiers)
// ---------------------------------------------------------------------------

/** Escape single quotes in SQL string literals */
function escapeString(value: string): string {
  return value.replace(/'/g, "''");
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
    session_end_reason: row.session_end_reason != undefined ? String(row.session_end_reason) : undefined,
    task_params: row.task_params != undefined ? String(row.task_params) : undefined,
    suites: row.suites != undefined ? String(row.suites) : undefined,
  };
}

/** Validate a raw query row into a ParquetRow */
function toParquetRow(row: Record<string, unknown>): ParquetRow {
  return {
    trajectory_id: String(row.trajectory_id ?? ""),
    session_id: row.session_id != undefined ? String(row.session_id) : undefined,
    agent: row.agent != undefined ? String(row.agent) : undefined,
    agent_model: row.agent_model != undefined ? String(row.agent_model) : undefined,
    started_at: row.started_at != undefined ? String(row.started_at) : undefined,
    environment: row.environment != undefined ? String(row.environment) : undefined,
    task_params: row.task_params != undefined ? String(row.task_params) : undefined,
    part: Number(row.part ?? 0),
    timestamp: row.timestamp != undefined ? String(row.timestamp) : undefined,
    role: row.role != undefined ? String(row.role) : undefined,
    part_type: row.part_type != undefined ? String(row.part_type) : undefined,
    item_type: row.item_type != undefined ? String(row.item_type) : undefined,
    summary: row.summary != undefined ? String(row.summary) : undefined,
    duration_ms: row.duration_ms != undefined ? Number(row.duration_ms) : undefined,
    git_commit: row.git_commit != undefined ? String(row.git_commit) : undefined,
    content: row.content != undefined ? String(row.content) : undefined,
    content_token_estimate: row.content_token_estimate != undefined ? Number(row.content_token_estimate) : undefined,
    tool_name: row.tool_name != undefined ? String(row.tool_name) : undefined,
    tool_status: row.tool_status != undefined ? String(row.tool_status) : undefined,
    tool_input: row.tool_input != undefined ? String(row.tool_input) : undefined,
    tool_output: row.tool_output != undefined ? String(row.tool_output) : undefined,
    tool_error: row.tool_error != undefined ? String(row.tool_error) : undefined,
    tool_exit_code: row.tool_exit_code != undefined ? Number(row.tool_exit_code) : undefined,
    token_usage: row.token_usage != undefined ? String(row.token_usage) : undefined,
    patch: row.patch != undefined ? String(row.patch) : undefined,
    repo_checkpoint: row.repo_checkpoint != undefined ? String(row.repo_checkpoint) : undefined,
    testing_state: row.testing_state != undefined ? String(row.testing_state) : undefined,
    eval_events_delta: row.eval_events_delta != undefined ? String(row.eval_events_delta) : undefined,
    turn: row.turn != undefined ? Number(row.turn) : undefined,
    session_end_reason: row.session_end_reason != undefined ? String(row.session_end_reason) : undefined,
    session_end_total_parts: row.session_end_total_parts != undefined ? Number(row.session_end_total_parts) : undefined,
    session_end_total_turns: row.session_end_total_turns != undefined ? Number(row.session_end_total_turns) : undefined,
    session_end_final_commit: row.session_end_final_commit != undefined ? String(row.session_end_final_commit) : undefined,
    suites: row.suites != undefined ? String(row.suites) : undefined,
    files: row.files != undefined ? String(row.files) : undefined,
    bundle_uri: row.bundle_uri != undefined ? String(row.bundle_uri) : undefined,
  };
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
}): Promise<Trajectory[]> {
  if (!isS3Configured()) {
    return getMockTrajectories();
  }

  const cacheKey = `all-trajectories:${opts?.environment ?? ""}:${opts?.model ?? ""}:${opts?.limit ?? ""}:${opts?.offset ?? ""}`;

  return cached(cacheKey, async () => {
    // Fast path: use materialized summary tables
    if (await hasSummaryTables()) {
      return getAllTrajectoriesFromSummary(opts);
    }
    // Slow path: GROUP BY scan of raw parquet files
    return getAllTrajectoriesFromGlob(opts);
  });
}

/**
 * Fast path: query pre-materialized trajectory_summary table.
 * Final scores are already embedded — no N+1 queries needed.
 */
async function getAllTrajectoriesFromSummary(opts?: {
  environment?: string;
  model?: string;
  limit?: number;
  offset?: number;
}): Promise<Trajectory[]> {
  let sql = `SELECT * FROM trajectory_summary WHERE 1=1`;

  if (opts?.environment) {
    sql += ` AND environment = '${escapeString(opts.environment)}'`;
  }
  if (opts?.model) {
    sql += ` AND agent_model = '${escapeString(opts.model)}'`;
  }

  sql += ` ORDER BY started_at DESC`;

  if (opts?.limit) {
    sql += ` LIMIT ${Number(opts.limit)}`;
  }
  if (opts?.offset) {
    sql += ` OFFSET ${Number(opts.offset)}`;
  }

  const rawRows = await query(sql);
  return rawRows.map((row) => summaryTableRowToTrajectory(toSummaryTableRow(row)));
}

/**
 * Slow path: GROUP BY scan of raw parquet files + N+1 evaluation queries.
 * Used when materialized summary tables are not available.
 */
async function getAllTrajectoriesFromGlob(opts?: {
  environment?: string;
  model?: string;
  limit?: number;
  offset?: number;
}): Promise<Trajectory[]> {
  const glob = await allTracesGlob();

  /**
   * Single query that computes trajectory summaries AND final evaluation scores
   * via a LEFT JOIN on the evaluations view. No N+1 queries.
   * Also computes duration from started_at and last timestamp, plus eval count.
   */
  let sql = `
    WITH summary AS (
      SELECT
        trajectory_id,
        agent_model,
        environment,
        MIN(agent) AS agent,
        MIN(started_at) AS started_at,
        MAX(timestamp) AS ended_at,
        MAX(part) + 1 AS total_parts,
        MAX(turn) AS total_turns,
        SUM(content_token_estimate) AS total_tokens,
        MAX(session_end_reason) AS session_end_reason,
        MIN(task_params) AS task_params,
        MIN(suites) AS suites
      FROM read_parquet('${escapeString(glob)}')
      GROUP BY trajectory_id, agent_model, environment
    ),
    best_eval AS (
      SELECT
        trajectory_id,
        MAX(passed) AS best_passed,
        MAX(failed) AS best_failed,
        MAX(total) AS best_total,
        COUNT(*) AS eval_count
      FROM evaluations
      WHERE status = 'completed'
      GROUP BY trajectory_id
    )
    SELECT
      s.*,
      b.best_passed,
      b.best_failed,
      b.best_total,
      b.eval_count
    FROM summary s
    LEFT JOIN best_eval b ON b.trajectory_id = s.trajectory_id
    WHERE 1=1
  `;

  if (opts?.environment) {
    sql += ` AND s.environment = '${escapeString(opts.environment)}'`;
  }
  if (opts?.model) {
    sql += ` AND s.agent_model = '${escapeString(opts.model)}'`;
  }

  sql += ` ORDER BY s.started_at DESC`;

  if (opts?.limit) {
    sql += ` LIMIT ${Number(opts.limit)}`;
  }
  if (opts?.offset) {
    sql += ` OFFSET ${Number(opts.offset)}`;
  }

  const rawRows = await query(sql);

  return rawRows.map((row) => {
    const summaryRow = toSummaryRow(row);
    const finalScore = row.best_passed !== undefined && row.best_passed !== null
      ? {
          passed: Number(row.best_passed ?? 0),
          failed: Number(row.best_failed ?? 0),
          total: Number(row.best_total ?? 0),
        }
      : undefined;

    return summaryRowToTrajectory(
      summaryRow,
      finalScore,
      String(row.ended_at ?? ""),
      Number(row.eval_count ?? 0),
    );
  });
}

/**
 * Get a single trajectory with full detail (all commits, steps, evaluations).
 */
export async function getTrajectoryById(
  id: string,
): Promise<Trajectory | undefined> {
  if (!isS3Configured()) {
    return getMockTrajectoryById(id);
  }

  try {
    const uri = await traceUri(id);
    const sql = `
      SELECT * FROM read_parquet('${escapeString(uri)}')
      ORDER BY part
    `;
    const rawRows = await query(sql);
    if (rawRows.length === 0) {
      return undefined;
    }
    const rows = rawRows.map(toParquetRow);
    return reconstructTrajectory(rows);
  } catch {
    return undefined;
  }
}

/**
 * Get evaluations for a trajectory (for progress curves).
 */
export async function getTrajectoryEvaluations(
  id: string,
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
        Object.entries(commit.suiteState).map(([key, val]) => [key, { passed: val, total: 0 }]),
      ),
      targetCommit: commit.hash,
    }));
  }

  try {
    const uri = await traceUri(id);
    const sql = `
      WITH events AS (
        SELECT
          part,
          unnest(
            from_json_strict(eval_events_delta, '["json"]')
          ) AS event
        FROM read_parquet('${escapeString(uri)}')
        WHERE eval_events_delta IS NOT NULL
          AND json_array_length(eval_events_delta) > 0
      )
      SELECT
        json_extract_string(event, '$.eval_id') AS eval_id,
        part,
        CAST(json_extract(event, '$.passed') AS INTEGER) AS passed,
        CAST(json_extract(event, '$.failed') AS INTEGER) AS failed,
        CAST(json_extract(event, '$.total') AS INTEGER) AS total,
        json_extract_string(event, '$.suite_results') AS suite_results,
        json_extract_string(event, '$.target_commit') AS target_commit
      FROM events
      WHERE json_extract_string(event, '$.status') = 'completed'
        AND json_extract_string(event, '$.kind') = 'commit_async'
      ORDER BY part
    `;
    const rawRows = await query(sql);

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
}

/**
 * Get distinct environments from the data.
 */
export async function getEnvironments(): Promise<
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

  return cached("environments", async () => {
    // Fast path: use summary table
    if (await hasSummaryTables()) {
      const sql = `
        SELECT
          environment,
          MIN(suites) AS suites,
          COUNT(*) AS trajectory_count,
          COUNT(DISTINCT agent_model) AS model_count
        FROM trajectory_summary
        GROUP BY environment
        ORDER BY environment
      `;
      const rawRows = await query(sql);
      return rawRows.map((row) => ({
        environment: String(row.environment ?? ""),
        suites: parseSuites(
          row.suites !== undefined ? String(row.suites) : undefined,
        ),
        trajectoryCount: Number(row.trajectory_count ?? 0),
        modelCount: Number(row.model_count ?? 0),
      }));
    }

    // Slow path: glob scan
    const glob = await allTracesGlob();
    const sql = `
      SELECT
        environment,
        MIN(suites) AS suites,
        COUNT(DISTINCT trajectory_id) AS trajectory_count,
        COUNT(DISTINCT agent_model) AS model_count
      FROM read_parquet('${escapeString(glob)}')
      GROUP BY environment
      ORDER BY environment
    `;
    const rawRows = await query(sql);
    return rawRows.map((row) => ({
      environment: String(row.environment ?? ""),
      suites: parseSuites(
        row.suites !== undefined ? String(row.suites) : undefined,
      ),
      trajectoryCount: Number(row.trajectory_count ?? 0),
      modelCount: Number(row.model_count ?? 0),
    }));
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
}): Promise<Trajectory[]> {
  if (!isS3Configured()) {
    const all = await getMockTrajectories();
    if (opts?.ids && opts.ids.length > 0) {
      const idSet = new Set(opts.ids);
      return all.filter((traj) => idSet.has(traj.id));
    }
    return all;
  }

  // Fast path: use summary + evaluation tables
  if (await hasSummaryTables()) {
    return getCompareTrajectoriesFromSummary(opts);
  }

  // Slow path: load full trajectories individually
  if (opts?.ids && opts.ids.length > 0) {
    const trajectories: Trajectory[] = [];
    for (const id of opts.ids) {
      const traj = await getTrajectoryById(id);
      if (traj) {
        trajectories.push(traj);
      }
    }
    return trajectories;
  }

  const glob = await allTracesGlob();
  const idSql = `
    SELECT DISTINCT trajectory_id
    FROM read_parquet('${escapeString(glob)}')
    ORDER BY trajectory_id
  `;
  const idRows = await query(idSql);
  const trajectories: Trajectory[] = [];
  for (const row of idRows) {
    const id = String(row.trajectory_id ?? "");
    if (!id) {
      continue;
    }
    const traj = await getTrajectoryById(id);
    if (traj) {
      trajectories.push(traj);
    }
  }
  return trajectories;
}

/**
 * Fast path for compare: query summary + evaluation tables in two bulk queries,
 * then build trajectories in memory via buildCompareTrajectories.
 */
async function getCompareTrajectoriesFromSummary(opts?: {
  ids?: string[];
  environment?: string;
}): Promise<Trajectory[]> {
  let summSql = `SELECT * FROM trajectory_summary WHERE 1=1`;
  let evalSql = `SELECT * FROM evaluation_summary WHERE 1=1`;

  if (opts?.ids && opts.ids.length > 0) {
    const idList = opts.ids.map((id) => `'${escapeString(id)}'`).join(", ");
    summSql += ` AND trajectory_id IN (${idList})`;
    evalSql += ` AND trajectory_id IN (${idList})`;
  }
  if (opts?.environment) {
    summSql += ` AND environment = '${escapeString(opts.environment)}'`;
    evalSql += ` AND environment = '${escapeString(opts.environment)}'`;
  }

  summSql += ` ORDER BY started_at DESC`;
  evalSql += ` ORDER BY trajectory_id, trigger_part`;

  const [summaryRows, evalRows] = await Promise.all([
    query(summSql),
    query(evalSql),
  ]);

  return buildCompareTrajectories(summaryRows, evalRows);
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
): Promise<Record<number, CodeSnapshot> | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }

  const uri = await codeSnapshotsUri(trajectoryId);
  if (uri === undefined) {
    return undefined;
  }

  try {
    const sql = `
      SELECT commit_hash, commit_index, file_path, status, content, added_lines
      FROM read_parquet('${escapeString(uri)}')
      ORDER BY commit_index, file_path
    `;
    const rawRows = await query(sql);

    const result: Record<number, CodeSnapshot> = {};

    for (const raw of rawRows) {
      const row = toCodeSnapshotRow(raw);
      const snapshot = result[row.commit_index];
      if (snapshot === undefined) {
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
    console.error(`[data] Failed to load code history for ${trajectoryId}:`, error);
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// Schema info (for SQL Console)
// ---------------------------------------------------------------------------

/**
 * Get database schema info — table/view names with their columns.
 * Used by the SQL Console to show a schema reference sidebar.
 */
export async function getSchemaInfo(): Promise<SchemaColumn[]> {
  if (!isS3Configured()) {
    return [];
  }

  try {
    const rawRows = await query(`
      SELECT table_name, column_name, data_type
      FROM information_schema.columns
      WHERE table_schema = 'main'
      ORDER BY table_name, ordinal_position
    `);

    return rawRows.map((row) => ({
      tableName: String(row.table_name ?? ""),
      columnName: String(row.column_name ?? ""),
      dataType: String(row.data_type ?? ""),
    }));
  } catch {
    return [];
  }
}

/**
 * Execute a read-only SQL query from the SQL Console.
 * Returns rows, column names, row count, and duration.
 */
export async function executeQuery(sql: string): Promise<{
  rows: Record<string, unknown>[];
  columns: string[];
  rowCount: number;
  durationMs: number;
}> {
  const start = Date.now();
  const rows = await query(sql);
  const durationMs = Date.now() - start;
  const columns = rows.length > 0 ? Object.keys(rows[0] ?? {}) : [];
  return { rows, columns, rowCount: rows.length, durationMs };
}

// ---------------------------------------------------------------------------
// Difficulty heatmap
// ---------------------------------------------------------------------------

/** Known c_compiler suites — everything else is gameboy emulator */
const C_COMPILER_SUITES = new Set(["basics", "wacct", "c_testsuite", "torture"]);

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
export async function getDifficultyData(): Promise<DifficultyCell[]> {
  if (!isS3Configured()) {
    return getMockDifficultyData();
  }

  return cached("difficulty-data", async () => {
    try {
      const glob = await allTracesGlob();

      /**
       * The `suites` column contains JSON like:
       *   {"all/basics/smoke": {"ok":true,"passed":7,"failed":0,"total":7}, ...}
       *
       * We take the latest non-empty suites snapshot per trajectory,
       * unnest the JSON keys, extract suite name (2nd path segment),
       * and aggregate passed/total per (suite, model).
       */
      const rawRows = await query(`
        WITH latest_suites AS (
          SELECT
            trajectory_id,
            agent_model,
            suites,
            ROW_NUMBER() OVER (
              PARTITION BY trajectory_id
              ORDER BY part DESC
            ) AS rn
          FROM read_parquet('${escapeString(glob)}')
          WHERE suites IS NOT NULL
            AND LENGTH(CAST(suites AS VARCHAR)) > 5
        ),
        snapshots AS (
          SELECT trajectory_id, agent_model, suites::JSON AS sr
          FROM latest_suites
          WHERE rn = 1
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
      `);

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
export async function getPortfolioData(): Promise<PortfolioRow[]> {
  if (!isS3Configured()) {
    return getMockPortfolioData();
  }

  return cached("portfolio-data", async () => {
    try {
      const glob = await allTracesGlob();

      /**
       * Get the latest suites snapshot per trajectory, compute overall
       * pass rate per trajectory, then derive environment from the first suite key.
       */
      const rawRows = await query(`
        WITH latest_suites AS (
          SELECT
            trajectory_id,
            agent_model,
            suites,
            ROW_NUMBER() OVER (
              PARTITION BY trajectory_id
              ORDER BY part DESC
            ) AS rn
          FROM read_parquet('${escapeString(glob)}')
          WHERE suites IS NOT NULL
            AND LENGTH(CAST(suites AS VARCHAR)) > 5
        ),
        snapshots AS (
          SELECT
            trajectory_id,
            agent_model,
            suites::JSON AS sr
          FROM latest_suites
          WHERE rn = 1
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
      `);

      return buildPortfolioRows(rawRows);
    } catch {
      return getMockPortfolioData();
    }
  });
}

/** Build PortfolioRow[] from ranked query results */
function buildPortfolioRows(rawRows: Record<string, unknown>[]): PortfolioRow[] {
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
    entry.avgRank = ranks.length > 0
      ? ranks.reduce((sum, rank) => sum + rank, 0) / ranks.length
      : 0;
  }

  return Array.from(modelMap.values()).sort((rowA, rowB) => rowA.avgRank - rowB.avgRank);
}

/** Mock portfolio data */
async function getMockPortfolioData(): Promise<PortfolioRow[]> {
  const models = ["gpt-4o", "claude-sonnet-4-20250514", "o3", "gemini-2.5-pro", "deepseek-r1"];
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
export async function getPortfolioEnvironmentData(): Promise<PortfolioEnvironmentRow[]> {
  if (!isS3Configured()) {
    return getMockPortfolioEnvironmentData();
  }

  return cached("portfolio-env-data", async () => {
    try {
      const rawRows = await query(`
        WITH best_eval AS (
          SELECT
            trajectory_id,
            MAX(passed) AS best_passed,
            MAX(total) AS best_total
          FROM evaluations
          WHERE status = 'completed'
          GROUP BY trajectory_id
        ),
        traj_scores AS (
          SELECT
            t.trajectory_id,
            t.agent_model,
            t.environment,
            t.total_tokens,
            COALESCE(b.best_passed, 0) AS best_passed,
            COALESCE(b.best_total, 0) AS best_total
          FROM trajectories t
          LEFT JOIN best_eval b ON b.trajectory_id = t.trajectory_id
        ),
        env_summary AS (
          SELECT
            environment,
            COUNT(*) AS run_count,
            MAX(best_passed) AS max_passed,
            MAX(best_total) AS max_total,
            MEDIAN(CASE WHEN best_total > 0 THEN best_passed * 1.0 / best_total ELSE 0 END) AS median_pass_rate,
            SUM(total_tokens) AS total_tokens,
            (SELECT agent_model FROM traj_scores ts2
              WHERE ts2.environment = traj_scores.environment
              ORDER BY ts2.best_passed DESC
              LIMIT 1) AS best_model,
            LIST(DISTINCT agent_model) AS model_list
          FROM traj_scores
          GROUP BY environment
        )
        SELECT * FROM env_summary
        ORDER BY environment
      `);

      return rawRows.map((row) => {
        /** Count runs per model from the data */
        const perModelCounts: Record<string, number> = {};
        return {
          environment: String(row.environment ?? ""),
          bestPassed: Number(row.max_passed ?? 0),
          bestTotal: Number(row.max_total ?? 0),
          bestModel: String(row.best_model ?? ""),
          medianPassRate: Number(row.median_pass_rate ?? 0),
          runCount: Number(row.run_count ?? 0),
          totalTokens: Number(row.total_tokens ?? 0),
          perModelCounts,
        };
      });
    } catch {
      return getMockPortfolioEnvironmentData();
    }
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
      perModelCounts: { "claude-code/opus-4.6": 8, "codex/gpt-5.3-codex": 6, "opencode/glm-5": 6 },
    },
    {
      environment: "gameboy_emulator",
      bestPassed: 156,
      bestTotal: 400,
      bestModel: "codex/gpt-5.3-codex",
      medianPassRate: 0.22,
      runCount: 10,
      totalTokens: 22_000_000,
      perModelCounts: { "claude-code/opus-4.6": 4, "codex/gpt-5.3-codex": 3, "opencode/glm-5": 3 },
    },
  ];
}

/**
 * Get Pareto frontier data — one point per trajectory with cost and score.
 * Optionally filter by environment.
 */
export async function getParetoData(environment?: string): Promise<ParetoPoint[]> {
  if (!isS3Configured()) {
    return getMockParetoData();
  }

  const cacheKey = `pareto-data:${environment ?? "all"}`;

  return cached(cacheKey, async () => {
    try {
      let sql = `
        WITH best_eval AS (
          SELECT
            trajectory_id,
            MAX(passed) AS best_passed,
            MAX(total) AS best_total
          FROM evaluations
          WHERE status = 'completed'
          GROUP BY trajectory_id
        )
        SELECT
          t.trajectory_id,
          t.agent_model,
          t.environment,
          t.total_tokens,
          COALESCE(b.best_passed, 0) AS best_passed,
          COALESCE(b.best_total, 0) AS best_total
        FROM trajectories t
        LEFT JOIN best_eval b ON b.trajectory_id = t.trajectory_id
        WHERE COALESCE(b.best_total, 0) > 0
      `;

      if (environment) {
        sql += ` AND t.environment = '${escapeString(environment)}'`;
      }

      sql += ` ORDER BY t.total_tokens ASC`;

      const rawRows = await query(sql);

      return rawRows.map((row) => {
        const passed = Number(row.best_passed ?? 0);
        const total = Number(row.best_total ?? 0);
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
  });
}

/** Mock Pareto data */
function getMockParetoData(): ParetoPoint[] {
  const models = ["claude-code/opus-4.6", "codex/gpt-5.3-codex", "opencode/glm-5"];
  const points: ParetoPoint[] = [];
  for (let pointIdx = 0; pointIdx < 20; pointIdx++) {
    const model = models[pointIdx % models.length] ?? models[0] ?? "";
    const tokens = 500_000 + pointIdx * 200_000 + Math.round(Math.random() * 500_000);
    const passRate = Math.min(0.95, 0.1 + pointIdx * 0.04 + Math.random() * 0.1);
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

