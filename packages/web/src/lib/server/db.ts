/**
 * DuckDB server-side singleton module.
 * Project-scoped: reads/writes under s3://<prefix>/project/<name>/trajectories/.
 */

import { DuckDBInstance } from "@duckdb/node-api";
import { execFileSync, execSync } from "node:child_process";
import { mkdir, readdir, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { cookies } from "next/headers";

let instance: DuckDBInstance | undefined;
let initPromise: Promise<DuckDBInstance> | undefined;
let loadedProject: string | undefined;
let refreshInterval: ReturnType<typeof setInterval> | undefined;
let warnedLegacyBucket = false;
const syncedProjects = new Set<string>();

const DB_PATH = path.resolve(process.cwd(), ".cache", "envoi.duckdb");
const SYNC_COOLDOWN_MS = 5 * 60_000;
const BACKGROUND_REFRESH_MS = 5 * 60_000;
const PROJECT_COOKIE = "envoi:project";

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

function syncStampPath(project: string): string {
  return path.resolve(process.cwd(), ".cache", `last-s3-sync-${project}`);
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

async function ensureDbDir(): Promise<void> {
  await mkdir(path.dirname(DB_PATH), { recursive: true });
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

function syncFromS3(project: string): void {
  if (!isS3Configured()) {
    return;
  }

  const prefix = getPrefix();
  const source = `s3://${prefix}/project/${project}/trajectories/`;
  const dest = cacheDir(project);

  try {
    console.log(`[db] Syncing traces from ${source}`);
    execSync(
      `aws s3 sync "${source}" "${dest}" --exclude "*" --include "*/trace.parquet"`,
      {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 60_000,
        env: awsEnv(),
      },
    );
  } catch (error) {
    console.warn(
      "[db] S3 trace sync failed, will fallback to remote parquet reads:",
      error,
    );
  }

  try {
    execSync(
      `aws s3 sync "${source}" "${dest}" --exclude "*" --include "summaries/*.parquet"`,
      {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 60_000,
        env: awsEnv(),
      },
    );
  } catch {
    // summary files optional
  }

  try {
    execSync(
      `aws s3 sync "${source}" "${dest}" --exclude "*" --include "*/code_snapshots.parquet"`,
      {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 60_000,
        env: awsEnv(),
      },
    );
  } catch {
    // code snapshots optional
  }
}

async function hasLocalCache(project: string): Promise<boolean> {
  const root = cacheDir(project);
  try {
    const dirs = await readdir(root);
    for (const dirName of dirs) {
      if (await pathExists(path.join(root, dirName, "trace.parquet"))) {
        return true;
      }
    }
    return false;
  } catch {
    return false;
  }
}

async function isSyncFresh(project: string): Promise<boolean> {
  try {
    const st = await stat(syncStampPath(project));
    return Date.now() - st.mtimeMs < SYNC_COOLDOWN_MS;
  } catch {
    return false;
  }
}

async function touchSyncStamp(project: string): Promise<void> {
  const stamp = syncStampPath(project);
  await mkdir(path.dirname(stamp), { recursive: true });
  await writeFile(stamp, String(Date.now()));
}

async function ensureSynced(project: string): Promise<void> {
  if (syncedProjects.has(project)) {
    return;
  }

  syncedProjects.add(project);
  if (await isSyncFresh(project)) {
    return;
  }

  syncFromS3(project);
  await touchSyncStamp(project);
}

/** Glob for all trace parquet files */
export async function allTracesGlob(project?: string): Promise<string> {
  const activeProject = await getActiveProject(project);
  await ensureSynced(activeProject);
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

/** URI for a single trajectory's parquet file */
export async function traceUri(
  trajectoryId: string,
  project?: string,
): Promise<string> {
  const activeProject = await getActiveProject(project);
  await ensureSynced(activeProject);
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

/** URI for code_snapshots.parquet (may not exist) */
export async function codeSnapshotsUri(
  trajectoryId: string,
  project?: string,
): Promise<string | undefined> {
  const activeProject = await getActiveProject(project);
  await ensureSynced(activeProject);
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
    const files: string[] = [];
    for (const dirName of dirs) {
      const traceFile = path.join(root, dirName, "trace.parquet");
      if (await pathExists(traceFile)) {
        files.push(traceFile);
      }
    }
    return files;
  } catch {
    return [];
  }
}

async function loadSummaryTables(
  inst: DuckDBInstance,
  project: string,
): Promise<void> {
  const summaryDir = path.join(cacheDir(project), "summaries");
  const trajSummaryPath = path.join(summaryDir, "trajectory_summary.parquet");
  const evalSummaryPath = path.join(summaryDir, "evaluation_summary.parquet");

  const conn = await inst.connect();
  try {
    await conn.run("DROP TABLE IF EXISTS trajectory_summary");
    await conn.run("DROP TABLE IF EXISTS evaluation_summary");

    if (await pathExists(trajSummaryPath)) {
      await conn.run(`
        CREATE TABLE trajectory_summary AS
        SELECT * FROM read_parquet('${trajSummaryPath}')
      `);
    }

    if (await pathExists(evalSummaryPath)) {
      await conn.run(`
        CREATE TABLE evaluation_summary AS
        SELECT * FROM read_parquet('${evalSummaryPath}')
      `);
    }
  } finally {
    conn.disconnectSync();
  }
}

async function createEmptyTables(inst: DuckDBInstance): Promise<void> {
  const conn = await inst.connect();
  try {
    await conn.run("DROP TABLE IF EXISTS trajectories");
    await conn.run("DROP TABLE IF EXISTS evaluations");
    await conn.run(`
      CREATE TABLE trajectories (
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
    `);
    await conn.run(`
      CREATE TABLE evaluations (
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
    `);
  } finally {
    conn.disconnectSync();
  }
}

async function createAnalyticsViews(
  inst: DuckDBInstance,
  project: string,
): Promise<void> {
  const files = await listTraceFiles(project);
  const conn = await inst.connect();
  try {
    await conn.run("DROP TABLE IF EXISTS evaluations");
    await conn.run("DROP TABLE IF EXISTS trajectories");
    await conn.run("DROP VIEW IF EXISTS evaluations");
    await conn.run("DROP VIEW IF EXISTS trajectories");
    await conn.run("DROP VIEW IF EXISTS turn_summaries");
    await conn.run("DROP VIEW IF EXISTS file_access");

    if (files.length === 0) {
      await createEmptyTables(inst);
      return;
    }

    await conn.run("SET threads=1");

    let firstTraj = true;
    for (const file of files) {
      const sql = `
        SELECT trajectory_id, environment, agent_model,
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
      if (firstTraj) {
        await conn.run(`CREATE TABLE trajectories AS ${sql}`);
        firstTraj = false;
      } else {
        await conn.run(`INSERT INTO trajectories ${sql}`);
      }
    }

    await conn.run(`
      CREATE TABLE evaluations (
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
    `);

    for (const file of files) {
      const rawResult = await conn.run(`
        SELECT trajectory_id, environment, agent_model, part, turn, git_commit, eval_events_delta
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
          const esc = (value: unknown): string =>
            String(value ?? "").replace(/'/g, "''");
          await conn.run(`
            INSERT INTO evaluations VALUES (
              '${esc(row.trajectory_id)}', '${esc(row.environment)}', '${esc(row.agent_model)}',
              ${Number(row.part ?? 0)},
              ${row.turn !== undefined && row.turn !== null ? Number(row.turn) : "NULL"},
              '${esc(row.git_commit)}', '${esc(evt.eval_id)}', '${esc(evt.status)}',
              ${Number(evt.passed ?? 0)}, ${Number(evt.failed ?? 0)}, ${Number(evt.total ?? 0)},
              '${esc(evt.target_commit)}', '${esc(JSON.stringify(evt.suite_results ?? {}))}',
              '${esc(evt.finished_at)}'
            )
          `);
        }
      }
    }

    await conn.run("SET threads=4");

    const glob = await allTracesGlob(project);
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
  } catch (error) {
    console.warn("[db] Failed to create analytics views:", error);
  } finally {
    conn.disconnectSync();
  }
}

async function createInstance(project: string): Promise<DuckDBInstance> {
  await ensureDbDir();
  await ensureCacheDir(project);
  await ensureSynced(project);

  const inst = await DuckDBInstance.create(DB_PATH);
  const cfgConn = await inst.connect();
  try {
    await cfgConn.run("SET memory_limit='1GB'");
    await cfgConn.run("SET threads=4");
    await cfgConn.run("SET preserve_insertion_order=false");
    await cfgConn.run(
      `SET temp_directory='${path.resolve(process.cwd(), ".cache", "duckdb_tmp")}'`,
    );
  } finally {
    cfgConn.disconnectSync();
  }

  if (isS3Configured() && !(await hasLocalCache(project))) {
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
    } finally {
      conn.disconnectSync();
    }
  }

  await loadSummaryTables(inst, project);
  await createAnalyticsViews(inst, project);
  loadedProject = project;
  return inst;
}

async function switchProject(project: string): Promise<void> {
  if (!instance) {
    return;
  }
  await ensureCacheDir(project);
  await ensureSynced(project);
  await loadSummaryTables(instance, project);
  await createAnalyticsViews(instance, project);
  loadedProject = project;
}

/** Get the shared DuckDB instance. Re-materializes tables on project switches. */
export async function getDb(project?: string): Promise<DuckDBInstance> {
  const activeProject = await getActiveProject(project);

  if (instance && loadedProject === activeProject) {
    return instance;
  }

  if (!instance && !initPromise) {
    initPromise = createInstance(activeProject).then((inst) => {
      instance = inst;
      initPromise = undefined;
      startBackgroundRefresh();
      return inst;
    });
    return initPromise;
  }

  if (initPromise) {
    const inst = await initPromise;
    if (loadedProject !== activeProject) {
      await switchProject(activeProject);
    }
    return inst;
  }

  await switchProject(activeProject);
  if (!instance) {
    throw new Error("DuckDB instance unavailable");
  }
  return instance;
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
  syncedProjects.delete(activeProject);
  try {
    await rm(syncStampPath(activeProject));
  } catch {
    // ignore
  }

  await ensureSynced(activeProject);
  const db = await getDb(activeProject);
  await loadSummaryTables(db, activeProject);
  await createAnalyticsViews(db, activeProject);
}

function startBackgroundRefresh(): void {
  if (refreshInterval) {
    return;
  }

  refreshInterval = setInterval(() => {
    if (!loadedProject) {
      return;
    }
    refreshData(loadedProject).catch((error) => {
      console.warn("[db] Background refresh failed:", error);
    });
  }, BACKGROUND_REFRESH_MS);

  if (refreshInterval.unref) {
    refreshInterval.unref();
  }
}

function runAwsCommand(args: string[], input?: string): string {
  const output = execFileSync("aws", args, {
    encoding: "utf8",
    env: awsEnv(),
    input,
    stdio: ["pipe", "pipe", "pipe"],
  });
  return output;
}

/** List all project prefixes and hydrate project.json metadata. */
export async function listProjects(): Promise<ProjectMeta[]> {
  if (!isS3Configured()) {
    return [];
  }

  const prefix = getPrefix();
  let raw = "";
  try {
    raw = runAwsCommand([
      "s3api",
      "list-objects-v2",
      "--bucket",
      prefix,
      "--prefix",
      "project/",
      "--delimiter",
      "/",
      "--output",
      "json",
    ]);
  } catch {
    return [];
  }

  let payload: { CommonPrefixes?: Array<{ Prefix?: string }> };
  try {
    payload = JSON.parse(raw);
  } catch {
    return [];
  }

  const names = (payload.CommonPrefixes ?? [])
    .map((entry) => entry.Prefix ?? "")
    .map((entry) => entry.replace(/^project\//, "").replace(/\/$/, ""))
    .filter((entry) => entry.length > 0);

  const metas = await Promise.all(names.map((name) => getProjectMeta(name)));
  return metas
    .filter((meta): meta is ProjectMeta => meta !== undefined)
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Load project.json metadata for a project. */
export async function getProjectMeta(
  project: string,
): Promise<ProjectMeta | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }

  const name = validateProjectName(project);
  const prefix = getPrefix();
  const uri = `s3://${prefix}/project/${name}/project.json`;
  try {
    const raw = runAwsCommand(["s3", "cp", uri, "-"]);
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) {
      return undefined;
    }
    const created = String(Reflect.get(parsed, "created_at") ?? "");
    const updated = String(Reflect.get(parsed, "updated_at") ?? "");
    const rawDescription = Reflect.get(parsed, "description");
    return {
      name,
      description:
        typeof rawDescription === "string" ? rawDescription : undefined,
      created_at: created,
      updated_at: updated,
    };
  } catch {
    return undefined;
  }
}

/** Write project.json metadata for a project. */
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

  const prefix = getPrefix();
  const uri = `s3://${prefix}/project/${name}/project.json`;
  runAwsCommand(["s3", "cp", "-", uri], JSON.stringify(payload, null, 2));
  return payload;
}
