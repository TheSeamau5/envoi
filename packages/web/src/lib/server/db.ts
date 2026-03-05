/**
 * DuckDB server-side singleton module.
 * Project-scoped: reads/writes under s3://<prefix>/project/<name>/trajectories/.
 */

import { DuckDBInstance } from "@duckdb/node-api";
import {
  S3Client,
  GetObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";
import { spawn } from "node:child_process";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { cookies } from "next/headers";
import { formatError, sqlLiteral } from "./utils";

let warnedLegacyBucket = false;
const instances = new Map<string, DuckDBInstance>();
const initPromises = new Map<string, Promise<DuckDBInstance>>();

/** Tracks in-flight and recently completed syncs to prevent spam. */
const syncInFlight = new Set<string>();
const lastSyncTime = new Map<string, number>();
const SYNC_MIN_INTERVAL_MS = 30_000;

const PROJECT_COOKIE = "envoi:project";
const PROJECTS_JSON_KEY = "projects.json";

function dbPath(project: string): string {
  return path.resolve(process.cwd(), ".cache", "duckdb", `${project}.duckdb`);
}

/** Lazy S3 client singleton — created on first use. */
let s3Client: S3Client | undefined;
function getS3Client(): S3Client {
  if (!s3Client) {
    s3Client = new S3Client({
      region: process.env.AWS_REGION ?? "us-east-1",
      credentials: {
        accessKeyId: process.env.AWS_ACCESS_KEY_ID ?? "",
        secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY ?? "",
      },
    });
  }
  return s3Client;
}

export type ProjectMeta = {
  name: string;
  description?: string;
  created_at: string;
  updated_at: string;
};

function validateProjectName(project: string): string {
  const value = project.trim();
  if (!/^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/.test(value)) {
    throw new Error(`Invalid project name: ${project}`);
  }
  return value;
}

function cacheDir(project: string): string {
  return path.resolve(
    process.cwd(),
    ".cache",
    "parquet",
    project,
    "trajectories",
  );
}

function summaryDir(project: string): string {
  return path.join(cacheDir(project), "summaries");
}

function trajectorySummaryPath(project: string): string {
  return path.join(summaryDir(project), "trajectory_summary.parquet");
}

function evaluationSummaryPath(project: string): string {
  return path.join(summaryDir(project), "evaluation_summary.parquet");
}

export function getPrefix(): string {
  const configured = (process.env.AWS_S3_PREFIX ?? "").trim();
  if (configured.length > 0) {
    return normalizePrefix(configured);
  }

  const legacy = (process.env.AWS_S3_BUCKET ?? "").trim();
  if (legacy.length > 0) {
    if (!warnedLegacyBucket) {
      console.warn("[db] AWS_S3_BUCKET is deprecated; use AWS_S3_PREFIX");
      warnedLegacyBucket = true;
    }
    return normalizePrefix(legacy);
  }

  return "";
}

function normalizePrefix(rawValue: string): string {
  let value = rawValue.trim();
  if (value.startsWith("s3://")) {
    value = value.slice("s3://".length);
  }

  value = value.replace(/^\/+/, "").replace(/\/+$/, "");
  if (!value) {
    return "";
  }

  if (value.includes("/")) {
    throw new Error(
      "AWS_S3_PREFIX must be a bucket name or s3://<bucket> (without path)",
    );
  }

  return value;
}

/** Whether S3 credentials are configured */
export function isS3Configured(): boolean {
  return getPrefix().length > 0;
}

export async function getActiveProject(project?: string): Promise<string> {
  if (project && project.trim().length > 0) {
    return validateProjectName(project);
  }

  try {
    const jar = await cookies();
    const fromCookie = jar.get(PROJECT_COOKIE)?.value ?? "";
    if (fromCookie.trim().length > 0) {
      return validateProjectName(fromCookie);
    }
  } catch {
    // no request context
  }

  const fromEnv = (process.env.ENVOI_PROJECT ?? "default").trim() || "default";
  return validateProjectName(fromEnv);
}

async function pathExists(filePath: string): Promise<boolean> {
  try {
    await stat(filePath);
    return true;
  } catch {
    return false;
  }
}

async function ensureCacheDir(project: string): Promise<void> {
  await mkdir(cacheDir(project), { recursive: true });
}

async function ensureDbDir(project: string): Promise<void> {
  await Promise.all([
    mkdir(path.dirname(dbPath(project)), { recursive: true }),
    mkdir(path.resolve(process.cwd(), ".cache", "duckdb_tmp"), {
      recursive: true,
    }),
  ]);
}

function awsEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  const accessKey = process.env.AWS_ACCESS_KEY_ID?.trim();
  const secretKey = process.env.AWS_SECRET_ACCESS_KEY?.trim();

  if (accessKey && secretKey) {
    env.AWS_ACCESS_KEY_ID = accessKey;
    env.AWS_SECRET_ACCESS_KEY = secretKey;
  }

  env.AWS_REGION = process.env.AWS_REGION ?? "us-east-1";
  return env;
}

/** Run a single `aws s3 sync` command as a child process, returning a promise. */
function spawnS3Sync(
  source: string,
  dest: string,
  include: string,
): Promise<void> {
  return new Promise((resolve) => {
    const child = spawn(
      "aws",
      ["s3", "sync", source, dest, "--exclude", "*", "--include", include],
      { stdio: ["ignore", "pipe", "pipe"], env: awsEnv() },
    );
    child.on("close", () => resolve());
    child.on("error", () => resolve());
  });
}

/**
 * Sync project data from S3 to local cache — runs as background child processes.
 * Returns a promise that resolves when all syncs complete.
 */
async function syncFromS3(
  project: string,
  opts?: {
    traces?: boolean;
    summaries?: boolean;
    codeSnapshots?: boolean;
  },
): Promise<void> {
  if (!isS3Configured()) {
    return;
  }

  const prefix = getPrefix();
  const source = `s3://${prefix}/project/${project}/trajectories/`;
  const dest = cacheDir(project);
  const tasks: Promise<void>[] = [];

  if (opts?.traces !== false) {
    tasks.push(spawnS3Sync(source, dest, "*/trace.parquet"));
  }
  if (opts?.summaries !== false) {
    tasks.push(spawnS3Sync(source, dest, "summaries/*.parquet"));
  }
  if (opts?.codeSnapshots !== false) {
    tasks.push(spawnS3Sync(source, dest, "*/code_snapshots.parquet"));
  }
  if (tasks.length === 0) {
    return;
  }

  console.log(`[db] Background sync from ${source}`);
  await Promise.all(tasks);
  console.log(`[db] Background sync complete for ${project}`);
}

async function hasLocalCache(project: string): Promise<boolean> {
  const root = cacheDir(project);
  try {
    const dirs = await readdir(root);
    const checks = await Promise.all(
      dirs.map((dirName) =>
        pathExists(path.join(root, dirName, "trace.parquet")),
      ),
    );
    return checks.some((exists) => exists);
  } catch {
    return false;
  }
}

async function hasLocalSummaryCache(project: string): Promise<boolean> {
  return pathExists(trajectorySummaryPath(project));
}

/**
 * Ensure project data is available locally.
 * - If local cache exists: return immediately, kick off background sync.
 * - If no local cache (first load): await the sync so we have data to show.
 */
async function ensureSynced(project: string): Promise<void> {
  await ensureCacheDir(project);

  if (await hasLocalCache(project)) {
    // Data exists locally — serve it now, maybe sync in background
    const lastSync = lastSyncTime.get(project) ?? 0;
    if (
      syncInFlight.has(project) ||
      Date.now() - lastSync < SYNC_MIN_INTERVAL_MS
    ) {
      return;
    }
    syncInFlight.add(project);
    syncFromS3(project)
      .then(async () => {
        lastSyncTime.set(project, Date.now());
        const inst = instances.get(project);
        if (inst) {
          await refreshProjectTables(inst, project, { allowPartial: true });
        }
      })
      .catch((error) => {
        console.warn("[db] Background sync failed:", error);
      })
      .finally(() => {
        syncInFlight.delete(project);
      });
    return;
  }

  // No local traces yet — sync summaries first for fast first paint.
  await syncFromS3(project, {
    traces: false,
    summaries: true,
    codeSnapshots: false,
  });
  const hasSummaries = await hasLocalSummaryCache(project);
  if (!hasSummaries) {
    // Fallback for older projects without summary parquet files.
    await syncFromS3(project, {
      traces: true,
      summaries: false,
      codeSnapshots: false,
    });
  }

  // Continue full sync in background so detail views stay local and fast.
  if (!syncInFlight.has(project)) {
    syncInFlight.add(project);
    syncFromS3(project)
      .then(async () => {
        lastSyncTime.set(project, Date.now());
        const inst = instances.get(project);
        if (inst) {
          await refreshProjectTables(inst, project, { allowPartial: true });
        }
      })
      .catch((error) => {
        console.warn("[db] Background sync failed:", error);
      })
      .finally(() => {
        syncInFlight.delete(project);
      });
  }
  lastSyncTime.set(project, Date.now());
}

/** Glob for all trace parquet files — uses local cache if available, else S3. */
export async function allTracesGlob(project?: string): Promise<string> {
  const activeProject = await getActiveProject(project);
  if (await hasLocalCache(activeProject)) {
    return path.join(cacheDir(activeProject), "*", "trace.parquet");
  }
  return `s3://${getPrefix()}/project/${activeProject}/trajectories/*/trace.parquet`;
}

/** Validate a trajectory ID to prevent path injection. */
export function validateTrajectoryId(id: string): string {
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
    throw new Error(`Invalid trajectory ID: ${id}`);
  }
  return id;
}

/** URI for a single trajectory's parquet file — uses local cache if available, else S3. */
export async function traceUri(
  trajectoryId: string,
  project?: string,
): Promise<string> {
  const activeProject = await getActiveProject(project);
  const validId = validateTrajectoryId(trajectoryId);
  const localPath = path.join(
    cacheDir(activeProject),
    validId,
    "trace.parquet",
  );
  if (await pathExists(localPath)) {
    return localPath;
  }
  return `s3://${getPrefix()}/project/${activeProject}/trajectories/${validId}/trace.parquet`;
}

/** Always return S3 URI, bypassing local cache */
export async function freshTraceUri(
  trajectoryId: string,
  project?: string,
): Promise<string> {
  const activeProject = await getActiveProject(project);
  const validId = validateTrajectoryId(trajectoryId);
  return `s3://${getPrefix()}/project/${activeProject}/trajectories/${validId}/trace.parquet`;
}

/** URI for code_snapshots.parquet (may not exist) — checks local cache only. */
export async function codeSnapshotsUri(
  trajectoryId: string,
  project?: string,
): Promise<string | undefined> {
  const activeProject = await getActiveProject(project);
  const validId = validateTrajectoryId(trajectoryId);
  const localPath = path.join(
    cacheDir(activeProject),
    validId,
    "code_snapshots.parquet",
  );
  if (await pathExists(localPath)) {
    return localPath;
  }
  return undefined;
}

async function listTraceFiles(project: string): Promise<string[]> {
  const root = cacheDir(project);
  try {
    const dirs = await readdir(root);
    const files = await Promise.all(
      dirs.map(async (dirName) => {
        const traceFile = path.join(root, dirName, "trace.parquet");
        return (await pathExists(traceFile)) ? traceFile : undefined;
      }),
    );
    return files.filter((file): file is string => file !== undefined);
  } catch {
    return [];
  }
}

async function loadSummaryTables(
  inst: DuckDBInstance,
  project: string,
): Promise<void> {
  const trajSummaryPath = trajectorySummaryPath(project);
  const evalSummaryPath = evaluationSummaryPath(project);
  const [hasTrajectorySummary, hasEvaluationSummary] = await Promise.all([
    pathExists(trajSummaryPath),
    pathExists(evalSummaryPath),
  ]);

  const conn = await inst.connect();
  try {
    await conn.run("DROP TABLE IF EXISTS trajectory_summary");
    await conn.run("DROP TABLE IF EXISTS evaluation_summary");

    if (hasTrajectorySummary) {
      await conn.run(`
        CREATE TABLE trajectory_summary AS
        SELECT * FROM read_parquet('${sqlLiteral(trajSummaryPath)}')
      `);
    }

    if (hasEvaluationSummary) {
      await conn.run(`
        CREATE TABLE evaluation_summary AS
        SELECT * FROM read_parquet('${sqlLiteral(evalSummaryPath)}')
      `);
    }
  } finally {
    conn.disconnectSync();
  }
}

function createTrajectoriesSchemaSql(tableName: string): string {
  return `
    CREATE TABLE ${tableName} (
      trajectory_id VARCHAR,
      environment VARCHAR,
      agent_model VARCHAR,
      agent VARCHAR,
      started_at VARCHAR,
      ended_at VARCHAR,
      total_parts BIGINT,
      total_turns BIGINT,
      total_tokens BIGINT,
      session_end_reason VARCHAR,
      task_params VARCHAR,
      suites VARCHAR
    )
  `;
}

function createEvaluationsSchemaSql(tableName: string): string {
  return `
    CREATE TABLE ${tableName} (
      trajectory_id VARCHAR,
      environment VARCHAR,
      agent_model VARCHAR,
      part INTEGER,
      turn INTEGER,
      git_commit VARCHAR,
      eval_id VARCHAR,
      status VARCHAR,
      passed INTEGER,
      failed INTEGER,
      total INTEGER,
      target_commit VARCHAR,
      suite_results VARCHAR,
      finished_at VARCHAR
    )
  `;
}

async function rebuildTrajectoriesFromTraceFiles(
  project: string,
  targetTable: string,
  conn: Awaited<ReturnType<DuckDBInstance["connect"]>>,
): Promise<void> {
  const files = await listTraceFiles(project);
  if (files.length === 0) {
    await conn.run(createTrajectoriesSchemaSql(targetTable));
    return;
  }

  let firstTrajectory = true;
  for (const file of files) {
    const sql = `
      SELECT
        trajectory_id,
        environment,
        agent_model,
        MIN(agent) AS agent,
        MIN(started_at) AS started_at,
        MAX(timestamp) AS ended_at,
        MAX(part) + 1 AS total_parts,
        MAX(turn) AS total_turns,
        SUM(content_token_estimate) AS total_tokens,
        MAX(session_end_reason) AS session_end_reason,
        MIN(task_params) AS task_params,
        arg_max(suites, part) AS suites
      FROM read_parquet('${file}')
      GROUP BY trajectory_id, environment, agent_model
    `;
    if (firstTrajectory) {
      await conn.run(`CREATE TABLE ${targetTable} AS ${sql}`);
      firstTrajectory = false;
      continue;
    }
    await conn.run(`INSERT INTO ${targetTable} ${sql}`);
  }
}

async function createAnalyticsViews(
  inst: DuckDBInstance,
  project: string,
): Promise<void> {
  const conn = await inst.connect();
  const nextTrajectoriesTable = "trajectories_next";
  const nextEvaluationsTable = "evaluations_next";
  const [hasTrajectorySummary, hasEvaluationSummary] = await Promise.all([
    pathExists(trajectorySummaryPath(project)),
    pathExists(evaluationSummaryPath(project)),
  ]);
  try {
    await conn.run("SET threads=1");
    await conn.run("SET preserve_insertion_order=false");

    await conn.run("DROP VIEW IF EXISTS turn_summaries");
    await conn.run("DROP VIEW IF EXISTS file_access");
    await conn.run(`DROP TABLE IF EXISTS ${nextTrajectoriesTable}`);
    await conn.run(`DROP TABLE IF EXISTS ${nextEvaluationsTable}`);

    if (hasTrajectorySummary) {
      const summaryPath = sqlLiteral(trajectorySummaryPath(project));
      await conn.run(`
        CREATE TABLE ${nextTrajectoriesTable} AS
        SELECT
          trajectory_id,
          environment,
          agent_model,
          agent,
          started_at,
          ended_at,
          total_parts,
          total_turns,
          total_tokens,
          session_end_reason,
          task_params,
          suites
        FROM read_parquet('${summaryPath}')
      `);
    } else {
      await rebuildTrajectoriesFromTraceFiles(
        project,
        nextTrajectoriesTable,
        conn,
      );
    }

    if (hasEvaluationSummary) {
      const summaryPath = sqlLiteral(evaluationSummaryPath(project));
      await conn.run(`
        CREATE TABLE ${nextEvaluationsTable} AS
        SELECT
          trajectory_id,
          environment,
          agent_model,
          CAST(trigger_part AS INTEGER) AS part,
          CAST(trigger_turn AS INTEGER) AS turn,
          target_commit AS git_commit,
          eval_id,
          status,
          CAST(passed AS INTEGER) AS passed,
          CAST(failed AS INTEGER) AS failed,
          CAST(total AS INTEGER) AS total,
          target_commit,
          suite_results,
          finished_at
        FROM read_parquet('${summaryPath}')
      `);
    } else {
      await conn.run(createEvaluationsSchemaSql(nextEvaluationsTable));
    }

    await conn.run("BEGIN TRANSACTION");
    try {
      await conn.run("DROP TABLE IF EXISTS trajectories");
      await conn.run("DROP TABLE IF EXISTS evaluations");
      await conn.run(
        `ALTER TABLE ${nextTrajectoriesTable} RENAME TO trajectories`,
      );
      await conn.run(
        `ALTER TABLE ${nextEvaluationsTable} RENAME TO evaluations`,
      );
      await conn.run("COMMIT");
    } catch (error) {
      await conn.run("ROLLBACK");
      throw error;
    }

    const glob = await allTracesGlob(project);
    const escapedGlob = sqlLiteral(glob);
    try {
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
        FROM read_parquet('${escapedGlob}')
        WHERE turn IS NOT NULL
        GROUP BY trajectory_id, environment, agent_model, turn
      `);
    } catch (error) {
      console.warn("[db] Failed to refresh turn_summaries view:", error);
    }
    try {
      await conn.run(`
        CREATE OR REPLACE VIEW file_access AS
        SELECT
          trajectory_id, environment, agent_model, part, turn, tool_name,
          json_extract_string(tool_input, '$.file_path') AS file_path,
          content_token_estimate AS tokens, duration_ms
        FROM read_parquet('${escapedGlob}')
        WHERE tool_name IN ('Read', 'Write', 'Edit', 'file_read', 'file_write')
          AND tool_input IS NOT NULL
      `);
    } catch (error) {
      console.warn("[db] Failed to refresh file_access view:", error);
    }
  } finally {
    conn.disconnectSync();
  }
}

async function refreshProjectTables(
  inst: DuckDBInstance,
  project: string,
  opts?: { allowPartial?: boolean },
): Promise<void> {
  const tasks = [
    { name: "summary tables", run: loadSummaryTables(inst, project) },
    { name: "analytics tables", run: createAnalyticsViews(inst, project) },
  ] as const;
  const results = await Promise.allSettled(tasks.map((task) => task.run));
  const failures: Array<{ name: string; reason: unknown }> = [];
  for (let index = 0; index < results.length; index++) {
    const result = results[index];
    const task = tasks[index];
    if (!result || !task || result.status !== "rejected") {
      continue;
    }
    failures.push({ name: task.name, reason: result.reason });
  }

  if (failures.length === 0) {
    return;
  }

  if (opts?.allowPartial === true) {
    for (const failure of failures) {
      console.warn(
        `[db] Failed to refresh ${failure.name}:`,
        formatError(failure.reason),
      );
    }
    if (failures.length < tasks.length) {
      return;
    }
  }

  const reasons = failures
    .map((failure) => `${failure.name}: ${formatError(failure.reason)}`)
    .join("; ");
  throw new Error(
    `[db] Failed to refresh project tables (${project}) — ${reasons}`,
  );
}

async function createInstance(project: string): Promise<DuckDBInstance> {
  await Promise.all([ensureDbDir(project), ensureSynced(project)]);
  const projectDbPath = dbPath(project);

  // Remove stale DB + WAL to avoid catalog errors on WAL replay.
  // All data is rebuilt from parquet files, so nothing is lost.
  await Promise.all([
    rm(projectDbPath, { force: true }),
    rm(`${projectDbPath}.wal`, { force: true }),
  ]);

  const inst = await DuckDBInstance.create(projectDbPath);
  const cfgConn = await inst.connect();
  try {
    await cfgConn.run("SET memory_limit='960MB'");
    await cfgConn.run("SET threads=1");
    await cfgConn.run("SET preserve_insertion_order=false");
    await cfgConn.run(
      `SET temp_directory='${path.resolve(process.cwd(), ".cache", "duckdb_tmp")}'`,
    );
  } finally {
    cfgConn.disconnectSync();
  }

  if (isS3Configured()) {
    // Always load httpfs when S3 is configured — fresh reads bypass the
    // local cache and go directly to S3 even when a cached copy exists.
    const conn = await inst.connect();
    try {
      await conn.run("INSTALL httpfs");
      await conn.run("LOAD httpfs");
      await conn.run(
        `SET s3_region='${process.env.AWS_REGION ?? "us-east-1"}'`,
      );
      await conn.run(
        `SET s3_access_key_id='${process.env.AWS_ACCESS_KEY_ID ?? ""}'`,
      );
      await conn.run(
        `SET s3_secret_access_key='${process.env.AWS_SECRET_ACCESS_KEY ?? ""}'`,
      );
      // Disable ETag checks for live S3 reads. The parquet file is
      // overwritten on every part during a live run, causing ETag
      // mismatches that abort the query mid-read.
      await conn.run("SET unsafe_disable_etag_checks=true");
    } finally {
      conn.disconnectSync();
    }
  }

  await refreshProjectTables(inst, project, { allowPartial: true });
  return inst;
}

/** Get a cached per-project DuckDB instance. */
export async function getDb(project?: string): Promise<DuckDBInstance> {
  const activeProject = await getActiveProject(project);
  const existing = instances.get(activeProject);
  if (existing) {
    return existing;
  }

  const pending = initPromises.get(activeProject);
  if (pending) {
    return pending;
  }

  const initPromise = createInstance(activeProject)
    .then((inst) => {
      instances.set(activeProject, inst);
      initPromises.delete(activeProject);
      return inst;
    })
    .catch((error) => {
      initPromises.delete(activeProject);
      throw error;
    });
  initPromises.set(activeProject, initPromise);
  return initPromise;
}

/** Run a SQL query and return rows as plain objects. */
export async function query(
  sql: string,
  project?: string,
): Promise<Record<string, unknown>[]> {
  const db = await getDb(project);
  const conn = await db.connect();
  try {
    const result = await conn.run(sql);
    return await result.getRowObjectsJson();
  } finally {
    conn.disconnectSync();
  }
}

export type BindValue =
  | { type: "varchar"; value: string }
  | { type: "integer"; value: number };

/** Run a parameterized SQL query. */
export async function queryParams(
  sql: string,
  binds: BindValue[],
  project?: string,
): Promise<Record<string, unknown>[]> {
  const db = await getDb(project);
  const conn = await db.connect();
  try {
    const prepared = await conn.prepare(sql);
    for (let index = 0; index < binds.length; index++) {
      const bind = binds[index];
      if (!bind) {
        continue;
      }
      if (bind.type === "varchar") {
        prepared.bindVarchar(index + 1, bind.value);
      } else {
        prepared.bindInteger(index + 1, bind.value);
      }
    }
    const result = await prepared.run();
    return await result.getRowObjectsJson();
  } finally {
    conn.disconnectSync();
  }
}

/** Whether trajectory_summary exists and has rows. */
export async function hasSummaryTables(project?: string): Promise<boolean> {
  try {
    const rows = await query(
      "SELECT COUNT(*) AS n FROM trajectory_summary LIMIT 1",
      project,
    );
    return rows.length > 0 && Number(rows[0]?.n) > 0;
  } catch {
    return false;
  }
}

/** Re-sync from S3 and reload summary/materialized tables. */
export async function refreshData(project?: string): Promise<void> {
  const activeProject = await getActiveProject(project);
  syncInFlight.delete(activeProject);
  lastSyncTime.delete(activeProject);
  await ensureSynced(activeProject);
  const inst = instances.get(activeProject);
  if (inst) {
    await refreshProjectTables(inst, activeProject, { allowPartial: true });
  }
}

/**
 * Read the aggregate projects.json from S3.
 * Returns all project metadata in a single S3 GetObject call.
 */
async function readProjectsJson(): Promise<ProjectMeta[]> {
  const bucket = getPrefix();
  try {
    const response = await getS3Client().send(
      new GetObjectCommand({ Bucket: bucket, Key: PROJECTS_JSON_KEY }),
    );
    const body = await response.Body?.transformToString("utf-8");
    if (!body) {
      return [];
    }
    const parsed: unknown = JSON.parse(body);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter(
      (entry): entry is ProjectMeta =>
        typeof entry === "object" &&
        entry !== null &&
        typeof entry.name === "string",
    );
  } catch (error: unknown) {
    const code =
      error instanceof Error ? (error as { name?: string }).name : "";
    if (code === "NoSuchKey") {
      return [];
    }
    console.error("[db] readProjectsJson error:", error);
    return [];
  }
}

/**
 * Write the aggregate projects.json to S3.
 */
async function writeProjectsJson(projects: ProjectMeta[]): Promise<void> {
  const bucket = getPrefix();
  await getS3Client().send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: PROJECTS_JSON_KEY,
      Body: JSON.stringify(projects, null, 2),
      ContentType: "application/json",
    }),
  );
}

/** List all projects — single S3 read of projects.json. */
export async function listProjects(): Promise<ProjectMeta[]> {
  if (!isS3Configured()) {
    return [];
  }
  return readProjectsJson();
}

/** Load project.json metadata for a single project. */
export async function getProjectMeta(
  project: string,
): Promise<ProjectMeta | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }
  const name = validateProjectName(project);
  const all = await readProjectsJson();
  return all.find((meta) => meta.name === name);
}

/** Create a new project — adds to projects.json and writes individual project.json. */
export async function createProjectMeta(
  project: string,
  meta: { description?: string },
): Promise<ProjectMeta> {
  const name = validateProjectName(project);
  const now = new Date().toISOString();
  const payload: ProjectMeta = {
    name,
    description: meta.description,
    created_at: now,
    updated_at: now,
  };

  const all = await readProjectsJson();
  const filtered = all.filter((entry) => entry.name !== name);
  filtered.push(payload);
  await writeProjectsJson(filtered);

  // Also write individual project.json for backwards compatibility
  const bucket = getPrefix();
  await getS3Client().send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: `project/${name}/project.json`,
      Body: JSON.stringify(payload, null, 2),
      ContentType: "application/json",
    }),
  );

  return payload;
}
