/**
 * DuckDB server-side singleton module.
 *
 * On startup, syncs parquet files from S3 to a local cache directory so that
 * DuckDB queries run against local disk instead of over the network.
 * Uses a persistent DuckDB database for parquet metadata caching and
 * summary table storage across server restarts.
 *
 * Must only be imported from server-side code (API routes, server components).
 */

import { DuckDBInstance } from "@duckdb/node-api";
import { execSync } from "child_process";
import { statSync, writeFileSync, mkdirSync, unlinkSync } from "node:fs";
import { stat, mkdir, readdir } from "node:fs/promises";
import path from "path";

// Store instance on globalThis so it survives Next.js hot reloads in dev.
// Without this, Turbopack re-evaluates the module (resetting local vars)
// while the old DuckDB instance still holds the file lock in the same process.
const globalForDb = globalThis as unknown as {
  __duckdb?: DuckDBInstance;
  __duckdbInit?: Promise<DuckDBInstance>;
  __duckdbRefreshInterval?: ReturnType<typeof setInterval>;
};
let instance: DuckDBInstance | undefined = globalForDb.__duckdb;
let initPromise: Promise<DuckDBInstance> | undefined = globalForDb.__duckdbInit;

function getBucket(): string {
  return process.env.AWS_S3_BUCKET ?? "";
}

/** Whether S3 credentials are configured */
export function isS3Configured(): boolean {
  return !!(
    process.env.AWS_S3_BUCKET &&
    process.env.AWS_ACCESS_KEY_ID &&
    process.env.AWS_SECRET_ACCESS_KEY
  );
}

// ---------------------------------------------------------------------------
// Async filesystem helpers
// ---------------------------------------------------------------------------

/** Check if a path exists (async replacement for fs.existsSync) */
async function pathExists(filePath: string): Promise<boolean> {
  try {
    await stat(filePath);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Local parquet cache
// ---------------------------------------------------------------------------

const CACHE_DIR = path.resolve(process.cwd(), ".cache", "parquet", "trajectories");
const DB_PATH = path.resolve(process.cwd(), ".cache", "envoi.duckdb");

/** Ensure the local cache directory exists */
async function ensureCacheDir(): Promise<void> {
  await mkdir(CACHE_DIR, { recursive: true });
}

/** Ensure the directory for the persistent DuckDB file exists */
async function ensureDbDir(): Promise<void> {
  await mkdir(path.dirname(DB_PATH), { recursive: true });
}

/** Sync parquet files from S3 to local cache (blocking, runs once at startup) */
function syncFromS3(): void {
  if (!isS3Configured()) {
    return;
  }

  const bucket = getBucket();
  const s3Path = `s3://${bucket}/trajectories/`;
  const s3Env = {
    ...process.env,
    AWS_ACCESS_KEY_ID: process.env.AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY: process.env.AWS_SECRET_ACCESS_KEY,
    AWS_REGION: process.env.AWS_REGION ?? "us-east-1",
  };

  // Sync trace files
  try {
    console.log("[db] Syncing parquet files from S3...");
    execSync(
      `aws s3 sync "${s3Path}" "${CACHE_DIR}" --exclude "*" --include "*/trace.parquet"`,
      {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 60_000,
        env: s3Env,
      },
    );
    console.log("[db] S3 trace sync complete.");
  } catch (err) {
    console.warn("[db] S3 trace sync failed, will query S3 directly:", err);
  }

  // Sync summary files (may not exist if materialization hasn't run)
  try {
    execSync(
      `aws s3 sync "${s3Path}" "${CACHE_DIR}" --exclude "*" --include "summaries/*.parquet"`,
      {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 60_000,
        env: s3Env,
      },
    );
    console.log("[db] S3 summary sync complete.");
  } catch {
    console.log("[db] No summary files on S3 (or sync failed), skipping.");
  }

  // Sync code snapshot files (may not exist if --extract-code wasn't used)
  try {
    execSync(
      `aws s3 sync "${s3Path}" "${CACHE_DIR}" --exclude "*" --include "*/code_snapshots.parquet"`,
      {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 60_000,
        env: s3Env,
      },
    );
    console.log("[db] S3 code snapshots sync complete.");
  } catch {
    console.log("[db] No code snapshot files on S3 (or sync failed), skipping.");
  }
}

/** Whether local cache has parquet files */
async function hasLocalCache(): Promise<boolean> {
  try {
    const dirs = await readdir(CACHE_DIR);
    for (const dirName of dirs) {
      if (await pathExists(path.join(CACHE_DIR, dirName, "trace.parquet"))) {
        return true;
      }
    }
    return false;
  } catch {
    return false;
  }
}

/**
 * Sync cooldown: only re-sync from S3 if >5 minutes since last sync.
 * Uses a timestamp file so the cooldown survives across Next.js module reloads.
 */
const SYNC_COOLDOWN_MS = 5 * 60_000; // 5 minutes
const SYNC_STAMP_PATH = path.resolve(process.cwd(), ".cache", "last-s3-sync");
let synced = false;

function isSyncFresh(): boolean {
  try {
    const st = statSync(SYNC_STAMP_PATH);
    return Date.now() - st.mtimeMs < SYNC_COOLDOWN_MS;
  } catch {
    return false;
  }
}

function touchSyncStamp(): void {
  try {
    mkdirSync(path.dirname(SYNC_STAMP_PATH), { recursive: true });
    writeFileSync(SYNC_STAMP_PATH, String(Date.now()));
  } catch {
    // non-critical
  }
}

function ensureSynced(): void {
  if (synced) {
    return;
  }
  synced = true;
  // Skip S3 sync if we synced recently (survives module reloads)
  if (isSyncFresh()) {
    return;
  }
  syncFromS3();
  touchSyncStamp();
}

// ---------------------------------------------------------------------------
// URI helpers — point to local cache when available, S3 as fallback
// ---------------------------------------------------------------------------

/** Glob for all trace parquet files */
export async function allTracesGlob(): Promise<string> {
  ensureSynced();
  if (await hasLocalCache()) {
    return path.join(CACHE_DIR, "*", "trace.parquet");
  }
  return `s3://${getBucket()}/trajectories/*/trace.parquet`;
}

/**
 * Validate a trajectory ID to prevent path injection.
 * DuckDB table functions (read_parquet) take string literals, not bind
 * parameters, so we validate the ID format before interpolating it into paths.
 */
export function validateTrajectoryId(id: string): string {
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
    throw new Error(`Invalid trajectory ID: ${id}`);
  }
  return id;
}

/** URI for a single trajectory's parquet file */
export async function traceUri(trajectoryId: string): Promise<string> {
  ensureSynced();
  const validId = validateTrajectoryId(trajectoryId);
  const localPath = path.join(CACHE_DIR, validId, "trace.parquet");
  if (await pathExists(localPath)) {
    return localPath;
  }
  return `s3://${getBucket()}/trajectories/${validId}/trace.parquet`;
}

/** Always return the S3 URI, bypassing local cache (for live trajectories) */
export function freshTraceUri(trajectoryId: string): string {
  const validId = validateTrajectoryId(trajectoryId);
  return `s3://${getBucket()}/trajectories/${validId}/trace.parquet`;
}

/** URI for a trajectory's code_snapshots.parquet file (may not exist) */
export async function codeSnapshotsUri(trajectoryId: string): Promise<string | undefined> {
  ensureSynced();
  const validId = validateTrajectoryId(trajectoryId);
  const localPath = path.join(CACHE_DIR, validId, "code_snapshots.parquet");
  if (await pathExists(localPath)) {
    return localPath;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Summary table loading
// ---------------------------------------------------------------------------

/** Load materialized summary parquet files into DuckDB tables */
async function loadSummaryTables(inst: DuckDBInstance): Promise<void> {
  const summaryDir = path.join(CACHE_DIR, "summaries");
  const trajSummaryPath = path.join(summaryDir, "trajectory_summary.parquet");
  const evalSummaryPath = path.join(summaryDir, "evaluation_summary.parquet");

  const conn = await inst.connect();
  try {
    if (await pathExists(trajSummaryPath)) {
      await conn.run(`
        CREATE OR REPLACE TABLE trajectory_summary AS
        SELECT * FROM read_parquet('${trajSummaryPath}')
      `);
      console.log("[db] Loaded trajectory_summary table");
    }

    if (await pathExists(evalSummaryPath)) {
      await conn.run(`
        CREATE OR REPLACE TABLE evaluation_summary AS
        SELECT * FROM read_parquet('${evalSummaryPath}')
      `);
      console.log("[db] Loaded evaluation_summary table");
    }
  } finally {
    conn.disconnectSync();
  }
}

/** List all local trace parquet file paths */
async function listTraceFiles(): Promise<string[]> {
  try {
    const dirs = await readdir(CACHE_DIR);
    const files: string[] = [];
    for (const dirName of dirs) {
      const filePath = path.join(CACHE_DIR, dirName, "trace.parquet");
      if (await pathExists(filePath)) {
        files.push(filePath);
      }
    }
    return files;
  } catch {
    return [];
  }
}

/**
 * Materialize analytics tables from parquet files.
 *
 * Processes files one at a time to stay within 1GB memory.
 * Evaluations use JS-side JSON parsing (DuckDB's from_json_strict OOMs on
 * large parquet files), trajectories use DuckDB GROUP BY per file.
 */
async function createAnalyticsViews(inst: DuckDBInstance): Promise<void> {
  const files = await listTraceFiles();
  if (files.length === 0) {
    console.log("[db] No local parquet files, skipping analytics materialization.");
    return;
  }

  const glob = await allTracesGlob();
  const conn = await inst.connect();
  try {
    // Drop stale objects (TABLE or VIEW) so fresh CREATE works.
    // Must drop TABLE before VIEW — DuckDB errors if you DROP VIEW on a TABLE.
    await conn.run(`DROP TABLE IF EXISTS evaluations`);
    await conn.run(`DROP TABLE IF EXISTS trajectories`);
    await conn.run(`DROP VIEW IF EXISTS evaluations`);
    await conn.run(`DROP VIEW IF EXISTS trajectories`);

    // Use single thread during materialization to minimize memory
    await conn.run("SET threads=1");

    // --- Trajectories: GROUP BY per file, INSERT INTO ---
    let firstTraj = true;
    for (const file of files) {
      const sql = `
        SELECT trajectory_id, environment, agent_model,
          MIN(agent) AS agent, MIN(started_at) AS started_at,
          MAX(timestamp) AS ended_at, MAX(part) + 1 AS total_parts,
          MAX(turn) AS total_turns, SUM(content_token_estimate) AS total_tokens,
          MAX(session_end_reason) AS session_end_reason,
          MIN(task_params) AS task_params, arg_max(suites, part) AS suites
        FROM read_parquet('${file}')
        GROUP BY trajectory_id, environment, agent_model
      `;
      if (firstTraj) {
        await conn.run(`CREATE TABLE trajectories AS ${sql}`);
        firstTraj = false;
      } else {
        await conn.run(`INSERT INTO trajectories ${sql}`);
      }
    }
    console.log("[db] Materialized trajectories table");

    // --- Evaluations: read raw JSON strings per file, parse in JS, INSERT ---
    await conn.run(`
      CREATE TABLE evaluations (
        trajectory_id VARCHAR, environment VARCHAR, agent_model VARCHAR,
        part INTEGER, turn INTEGER, git_commit VARCHAR,
        eval_id VARCHAR, status VARCHAR, passed INTEGER, failed INTEGER,
        total INTEGER, target_commit VARCHAR, suite_results VARCHAR,
        finished_at VARCHAR
      )
    `);

    for (const file of files) {
      const rawResult = await conn.run(`
        SELECT trajectory_id, environment, agent_model, part, turn,
          git_commit, eval_events_delta
        FROM read_parquet('${file}')
        WHERE eval_events_delta IS NOT NULL
          AND json_array_length(eval_events_delta) > 0
      `);
      const rawRows = await rawResult.getRowObjectsJson();

      for (const row of rawRows) {
        let events: Record<string, unknown>[];
        try {
          events = JSON.parse(String(row.eval_events_delta));
        } catch {
          continue;
        }
        for (const evt of events) {
          const esc = (value: unknown) => String(value ?? "").replace(/'/g, "''");
          await conn.run(`
            INSERT INTO evaluations VALUES (
              '${esc(row.trajectory_id)}', '${esc(row.environment)}',
              '${esc(row.agent_model)}', ${Number(row.part ?? 0)},
              ${row.turn !== undefined && row.turn !== null ? Number(row.turn) : "NULL"},
              '${esc(row.git_commit)}', '${esc(evt.eval_id)}', '${esc(evt.status)}',
              ${Number(evt.passed ?? 0)}, ${Number(evt.failed ?? 0)},
              ${Number(evt.total ?? 0)}, '${esc(evt.target_commit)}',
              '${esc(JSON.stringify(evt.suite_results ?? {}))}',
              '${esc(evt.finished_at)}'
            )
          `);
        }
      }
    }
    console.log("[db] Materialized evaluations table");

    // Restore threads for normal query workloads
    await conn.run("SET threads=4");

    // Lightweight views — only evaluated when explicitly queried
    await conn.run(`
      CREATE OR REPLACE VIEW turn_summaries AS
      SELECT
        trajectory_id, environment, agent_model, turn,
        MIN(timestamp) AS turn_start, MAX(timestamp) AS turn_end,
        COUNT(*) AS num_parts,
        SUM(content_token_estimate) AS total_content_tokens,
        SUM(duration_ms) AS total_duration_ms,
        COUNT(CASE WHEN tool_name IS NOT NULL THEN 1 END) AS num_tool_calls,
        MAX(git_commit) AS last_commit
      FROM read_parquet('${glob}')
      WHERE turn IS NOT NULL
      GROUP BY trajectory_id, environment, agent_model, turn
    `);
    console.log("[db] Created turn_summaries view");

    await conn.run(`
      CREATE OR REPLACE VIEW file_access AS
      SELECT
        trajectory_id, environment, agent_model, part, turn, tool_name,
        json_extract_string(tool_input, '$.file_path') AS file_path,
        content_token_estimate AS tokens, duration_ms
      FROM read_parquet('${glob}')
      WHERE tool_name IN ('Read', 'Write', 'Edit', 'file_read', 'file_write')
        AND tool_input IS NOT NULL
    `);
    console.log("[db] Created file_access view");
  } catch (err) {
    console.warn("[db] Failed to create analytics views:", err);
  } finally {
    conn.disconnectSync();
  }
}

// ---------------------------------------------------------------------------
// DuckDB instance
// ---------------------------------------------------------------------------

async function createInstance(): Promise<DuckDBInstance> {
  await ensureDbDir();
  await ensureCacheDir();
  const inst = await DuckDBInstance.create(DB_PATH);

  // Cap memory and threads so DuckDB doesn't consume all system RAM
  const cfgConn = await inst.connect();
  try {
    await cfgConn.run("SET memory_limit='1GB'");
    await cfgConn.run("SET threads=4");
    await cfgConn.run("SET preserve_insertion_order=false");
    await cfgConn.run(`SET temp_directory='${path.resolve(process.cwd(), ".cache", "duckdb_tmp")}'`);
  } finally {
    cfgConn.disconnectSync();
  }

  // Only configure httpfs if we'll need S3 access (no local cache)
  if (isS3Configured() && !(await hasLocalCache())) {
    const conn = await inst.connect();
    try {
      await conn.run("INSTALL httpfs");
      await conn.run("LOAD httpfs");
      await conn.run(`SET s3_region='${process.env.AWS_REGION ?? "us-east-1"}'`);
      await conn.run(
        `SET s3_access_key_id='${process.env.AWS_ACCESS_KEY_ID ?? ""}'`,
      );
      await conn.run(
        `SET s3_secret_access_key='${process.env.AWS_SECRET_ACCESS_KEY ?? ""}'`,
      );
    } finally {
      conn.disconnectSync();
    }
  }

  // Load summary tables and create analytics views
  await loadSummaryTables(inst);
  await createAnalyticsViews(inst);

  return inst;
}

/** Get the shared DuckDB instance (creates on first call, survives hot reloads) */
export async function getDb(): Promise<DuckDBInstance> {
  if (instance) {
    return instance;
  }
  if (!initPromise) {
    initPromise = createInstance().then((inst) => {
      instance = inst;
      globalForDb.__duckdb = inst;
      globalForDb.__duckdbInit = undefined;
      startBackgroundRefresh();
      return inst;
    });
    globalForDb.__duckdbInit = initPromise;
  }
  return initPromise;
}

/**
 * Run a SQL query and return results as an array of plain objects.
 * Uses getRowObjectsJson() for clean JSON-compatible output.
 * Returns Record<string, unknown>[] — callers should validate/narrow the shape.
 */
export async function query(
  sql: string,
): Promise<Record<string, unknown>[]> {
  const db = await getDb();
  const conn = await db.connect();
  try {
    const result = await conn.run(sql);
    return await result.getRowObjectsJson();
  } finally {
    conn.disconnectSync();
  }
}

/**
 * Run a parameterized SQL query. Binds are positional ($1, $2, ...).
 * Each bind value is {type, value} so we can call the right bind method.
 *
 * Use this for every query that includes user-supplied filter values
 * (environment name, model name, trajectory ID, limit, offset).
 */
export type BindValue =
  | { type: "varchar"; value: string }
  | { type: "integer"; value: number };

export async function queryParams(
  sql: string,
  binds: BindValue[],
): Promise<Record<string, unknown>[]> {
  const db = await getDb();
  const conn = await db.connect();
  try {
    const prepared = await conn.prepare(sql);
    for (let index = 0; index < binds.length; index++) {
      const bind = binds[index];
      if (!bind) {
        continue;
      }
      switch (bind.type) {
        case "varchar":
          prepared.bindVarchar(index + 1, bind.value);
          break;
        case "integer":
          prepared.bindInteger(index + 1, bind.value);
          break;
      }
    }
    const result = await prepared.run();
    return await result.getRowObjectsJson();
  } finally {
    conn.disconnectSync();
  }
}

/**
 * Check whether the materialized summary tables exist in DuckDB.
 * The data layer uses this to decide between the fast path (summary tables)
 * and the slow path (glob scan of raw parquet files).
 */
export async function hasSummaryTables(): Promise<boolean> {
  try {
    const rows = await query(
      "SELECT COUNT(*) AS n FROM trajectory_summary LIMIT 1",
    );
    return rows.length > 0 && Number(rows[0]?.n) > 0;
  } catch {
    return false;
  }
}

/**
 * Re-run S3 sync and reload summary tables + materialized analytics tables.
 * Call from POST /api/refresh to pick up new data.
 */
export async function refreshData(): Promise<void> {
  synced = false;
  // Delete sync stamp so ensureSynced actually runs
  try {
    unlinkSync(SYNC_STAMP_PATH);
  } catch {
    // file may not exist
  }
  ensureSynced();
  const db = await getDb();
  await loadSummaryTables(db);
  await createAnalyticsViews(db);
}

// ---------------------------------------------------------------------------
// Background refresh — keeps materialized tables fresh without blocking UI
// ---------------------------------------------------------------------------

const BACKGROUND_REFRESH_MS = 5 * 60_000; // every 5 minutes

/** Start periodic background refresh (idempotent, survives hot reloads) */
function startBackgroundRefresh(): void {
  if (globalForDb.__duckdbRefreshInterval) {
    return;
  }
  globalForDb.__duckdbRefreshInterval = setInterval(() => {
    refreshData().catch((err) => {
      console.warn("[db] Background refresh failed:", err);
    });
  }, BACKGROUND_REFRESH_MS);
  // Don't prevent process exit
  if (globalForDb.__duckdbRefreshInterval.unref) {
    globalForDb.__duckdbRefreshInterval.unref();
  }
}
