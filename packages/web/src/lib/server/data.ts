/**
 * Server-side data access layer.
 *
 * Queries DuckDB/S3 for real data, falls back to mock when S3 is not configured.
 * Import this module only from server components or API route handlers.
 */

import {
  isS3Configured,
  allTracesGlob,
  traceUri,
  query,
} from "./db";
import {
  reconstructTrajectory,
  summaryRowToTrajectory,
  parseSuites,
  type ParquetRow,
  type TrajectorySummaryRow,
} from "./reconstruct";
import type { Trajectory, Suite } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mock fallback (lazy import to avoid bundling when not needed)
// ---------------------------------------------------------------------------

async function getMockTrajectories(): Promise<Trajectory[]> {
  const { generateAllTrajectories } = await import("@/lib/mock");
  return generateAllTrajectories();
}

async function getMockTrajectoryById(
  id: string,
): Promise<Trajectory | undefined> {
  const { getTrajectoryById } = await import("@/lib/mock");
  return getTrajectoryById(id);
}

// ---------------------------------------------------------------------------
// Escape helpers (prevent SQL injection for identifiers)
// ---------------------------------------------------------------------------

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
    session_end_reason: row.session_end_reason != null ? String(row.session_end_reason) : undefined,
    task_params: row.task_params != null ? String(row.task_params) : undefined,
    suites: row.suites != null ? String(row.suites) : undefined,
  };
}

/** Validate a raw query row into a ParquetRow */
function toParquetRow(row: Record<string, unknown>): ParquetRow {
  return {
    trajectory_id: String(row.trajectory_id ?? ""),
    session_id: row.session_id != null ? String(row.session_id) : undefined,
    agent: row.agent != null ? String(row.agent) : undefined,
    agent_model: row.agent_model != null ? String(row.agent_model) : undefined,
    started_at: row.started_at != null ? String(row.started_at) : undefined,
    environment: row.environment != null ? String(row.environment) : undefined,
    task_params: row.task_params != null ? String(row.task_params) : undefined,
    part: Number(row.part ?? 0),
    timestamp: row.timestamp != null ? String(row.timestamp) : undefined,
    role: row.role != null ? String(row.role) : undefined,
    part_type: row.part_type != null ? String(row.part_type) : undefined,
    item_type: row.item_type != null ? String(row.item_type) : undefined,
    summary: row.summary != null ? String(row.summary) : undefined,
    duration_ms: row.duration_ms != null ? Number(row.duration_ms) : undefined,
    git_commit: row.git_commit != null ? String(row.git_commit) : undefined,
    content: row.content != null ? String(row.content) : undefined,
    content_token_estimate: row.content_token_estimate != null ? Number(row.content_token_estimate) : undefined,
    tool_name: row.tool_name != null ? String(row.tool_name) : undefined,
    tool_status: row.tool_status != null ? String(row.tool_status) : undefined,
    tool_input: row.tool_input != null ? String(row.tool_input) : undefined,
    tool_output: row.tool_output != null ? String(row.tool_output) : undefined,
    tool_error: row.tool_error != null ? String(row.tool_error) : undefined,
    tool_exit_code: row.tool_exit_code != null ? Number(row.tool_exit_code) : undefined,
    token_usage: row.token_usage != null ? String(row.token_usage) : undefined,
    patch: row.patch != null ? String(row.patch) : undefined,
    repo_checkpoint: row.repo_checkpoint != null ? String(row.repo_checkpoint) : undefined,
    testing_state: row.testing_state != null ? String(row.testing_state) : undefined,
    eval_events_delta: row.eval_events_delta != null ? String(row.eval_events_delta) : undefined,
    turn: row.turn != null ? Number(row.turn) : undefined,
    session_end_reason: row.session_end_reason != null ? String(row.session_end_reason) : undefined,
    session_end_total_parts: row.session_end_total_parts != null ? Number(row.session_end_total_parts) : undefined,
    session_end_total_turns: row.session_end_total_turns != null ? Number(row.session_end_total_turns) : undefined,
    session_end_final_commit: row.session_end_final_commit != null ? String(row.session_end_final_commit) : undefined,
    suites: row.suites != null ? String(row.suites) : undefined,
    files: row.files != null ? String(row.files) : undefined,
    bundle_uri: row.bundle_uri != null ? String(row.bundle_uri) : undefined,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Get all trajectory summaries (lightweight, for list pages).
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

  const glob = allTracesGlob();

  // Build the trajectory summary query
  let sql = `
    WITH summary AS (
      SELECT
        trajectory_id,
        agent_model,
        environment,
        MIN(agent) AS agent,
        MIN(started_at) AS started_at,
        MAX(part) + 1 AS total_parts,
        MAX(turn) AS total_turns,
        SUM(content_token_estimate) AS total_tokens,
        MAX(session_end_reason) AS session_end_reason,
        MIN(task_params) AS task_params,
        MIN(suites) AS suites
      FROM read_parquet('${escapeString(glob)}')
      GROUP BY trajectory_id, agent_model, environment
    )
    SELECT * FROM summary
    WHERE 1=1
  `;

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
  const summaryRows = rawRows.map(toSummaryRow);

  // For each trajectory, get the final score from the last completed evaluation
  const trajectories: Trajectory[] = [];
  for (const row of summaryRows) {
    // Try to get final score
    let finalScore: { passed: number; failed: number; total: number } | undefined;
    try {
      const evalSql = `
        WITH events AS (
          SELECT
            unnest(
              from_json_strict(
                eval_events_delta,
                '["json"]'
              )
            ) AS event
          FROM read_parquet('${escapeString(traceUri(row.trajectory_id))}')
          WHERE eval_events_delta IS NOT NULL
            AND json_array_length(eval_events_delta) > 0
        )
        SELECT
          CAST(json_extract(event, '$.passed') AS INTEGER) AS passed,
          CAST(json_extract(event, '$.failed') AS INTEGER) AS failed,
          CAST(json_extract(event, '$.total') AS INTEGER) AS total
        FROM events
        WHERE json_extract_string(event, '$.status') = 'completed'
          AND json_extract_string(event, '$.kind') = 'commit_async'
        ORDER BY CAST(json_extract(event, '$.trigger_part') AS INTEGER) DESC
        LIMIT 1
      `;
      const evalRawRows = await query(evalSql);
      const evalRow = evalRawRows[0];
      if (evalRow) {
        finalScore = {
          passed: Number(evalRow.passed ?? 0),
          failed: Number(evalRow.failed ?? 0),
          total: Number(evalRow.total ?? 0),
        };
      }
    } catch {
      // Skip final score on error
    }

    trajectories.push(summaryRowToTrajectory(row, finalScore));
  }

  return trajectories;
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
    const uri = traceUri(id);
    const sql = `
      SELECT * FROM read_parquet('${escapeString(uri)}')
      ORDER BY part
    `;
    const rawRows = await query(sql);
    if (rawRows.length === 0) return undefined;
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
    // Derive from mock trajectory
    const traj = await getMockTrajectoryById(id);
    if (!traj) return [];
    return traj.commits.map((c, idx) => ({
      evalId: `mock-eval-${idx}`,
      part: idx,
      passed: c.totalPassed,
      failed: c.feedback.totalFailed,
      total: c.totalPassed + c.feedback.totalFailed,
      suiteResults: Object.fromEntries(
        Object.entries(c.suiteState).map(([k, v]) => [k, { passed: v, total: 0 }]),
      ),
      targetCommit: c.hash,
    }));
  }

  try {
    const uri = traceUri(id);
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

  const glob = allTracesGlob();
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
}

/**
 * Get trajectories for comparison.
 * Can filter by specific IDs or environment.
 */
export async function getCompareTrajectories(opts?: {
  ids?: string[];
  environment?: string;
}): Promise<Trajectory[]> {
  if (!isS3Configured()) {
    const all = await getMockTrajectories();
    if (opts?.ids && opts.ids.length > 0) {
      const idSet = new Set(opts.ids);
      return all.filter((t) => idSet.has(t.id));
    }
    return all;
  }

  // For compare, we need full trajectory data (with commits for curves)
  if (opts?.ids && opts.ids.length > 0) {
    const trajectories: Trajectory[] = [];
    for (const id of opts.ids) {
      const t = await getTrajectoryById(id);
      if (t) trajectories.push(t);
    }
    return trajectories;
  }

  // Otherwise fetch all (summary level is usually enough for compare)
  return getAllTrajectories({ environment: opts?.environment });
}
