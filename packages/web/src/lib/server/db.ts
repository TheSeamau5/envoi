/**
 * DuckDB server-side singleton module.
 * Project-scoped: reads/writes under s3://<prefix>/project/<name>/trajectories/.
 */

import { DuckDBInstance } from "@duckdb/node-api";
import {
  S3Client,
  GetObjectCommand,
  HeadObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";
import { spawn } from "node:child_process";
import {
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { cookies } from "next/headers";
import { clearCache } from "./cache";
import { formatError, sqlLiteral } from "./utils";

let warnedLegacyBucket = false;

// Store singletons on globalThis so they survive Turbopack HMR in dev mode.
// Without this, each module re-evaluation creates fresh Maps and loses the
// DuckDB instances warmed during instrumentation.
type DbGlobals = {
  envoiDbInstances: Map<string, DuckDBInstance>;
  envoiDbInitPromises: Map<string, Promise<DuckDBInstance>>;
  envoiSyncInFlight: Set<string>;
  envoiRawSyncPromises: Map<string, Promise<void>>;
  envoiLastSyncTime: Map<string, number>;
  envoiAnalyticsViewsLock: Map<string, Promise<void>>;
};
const g = globalThis as unknown as Partial<DbGlobals>;
const instances = (g.envoiDbInstances ??= new Map<string, DuckDBInstance>());
const initPromises = (g.envoiDbInitPromises ??= new Map<
  string,
  Promise<DuckDBInstance>
>());

/** Tracks in-flight and recently completed syncs to prevent spam. */
const syncInFlight = (g.envoiSyncInFlight ??= new Set<string>());
const rawSyncPromises = (g.envoiRawSyncPromises ??= new Map<
  string,
  Promise<void>
>());
const lastSyncTime = (g.envoiLastSyncTime ??= new Map<string, number>());
const SYNC_MIN_INTERVAL_MS = 30_000;
const SUMMARY_REVISION_POLL_MS = 5_000;

/** Per-project mutex for createAnalyticsViews to prevent concurrent table rebuilds. */
const analyticsViewsLock = (g.envoiAnalyticsViewsLock ??= new Map<
  string,
  Promise<void>
>());

const PROJECT_COOKIE = "envoi:project";
const PROJECTS_JSON_KEY = "projects.json";
const TRAJECTORY_SUMMARY_FILENAME = "trajectory_summary.parquet";
const EVALUATION_SUMMARY_FILENAME = "evaluation_summary.parquet";
const SUMMARY_MANIFEST_FILE = "manifest.json";

export type SummaryManifestFile = {
  path: string;
  sizeBytes: number;
  sha256: string;
};

export type SummaryManifest = {
  revision: string;
  publishedAt: string;
  trajectorySummary: SummaryManifestFile;
  evaluationSummary: SummaryManifestFile;
};

export type SummaryRevisionStatus = {
  hasManifest: boolean;
  inSync: boolean;
  s3Revision?: string;
  loadedRevision?: string;
  lastCheckedAt?: string;
  lastLoadedAt?: string;
  publishedAt?: string;
  revisionLagMs: number;
  refreshDurationMs?: number;
};

export type ProjectFreshnessMode = "cached" | "fresh" | "force";

export type ProjectDataStatus = SummaryRevisionStatus & {
  dataVersion: string;
  summaryRevision?: string;
  loadedSummaryRevision?: string;
  lastRawSyncAt?: string;
  lastTableRefreshAt?: string;
  rawSyncInFlight: boolean;
  summarySyncInFlight: boolean;
};

type SummaryRevisionState = {
  hasManifest: boolean;
  s3Revision?: string;
  loadedRevision?: string;
  manifest?: SummaryManifest;
  manifestEtag?: string;
  lastCheckedAtMs?: number;
  lastLoadedAtMs?: number;
  refreshDurationMs?: number;
};

type ProjectDataState = {
  lastRawSyncAtMs?: number;
  lastTableRefreshAtMs?: number;
  dataVersion?: string;
};

const summaryRevisionStates = new Map<string, SummaryRevisionState>();
const summaryRevisionInFlight = new Map<
  string,
  Promise<SummaryRevisionStatus>
>();
const projectDataStates = new Map<string, ProjectDataState>();

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

function summaryManifestPath(project: string): string {
  return path.join(summaryDir(project), SUMMARY_MANIFEST_FILE);
}

function summaryObjectKey(project: string, filename: string): string {
  return `project/${project}/trajectories/summaries/${filename}`;
}

function getSummaryRevisionState(project: string): SummaryRevisionState {
  const existing = summaryRevisionStates.get(project);
  if (existing) {
    return existing;
  }
  const created: SummaryRevisionState = {
    hasManifest: false,
  };
  summaryRevisionStates.set(project, created);
  return created;
}

function getProjectDataState(project: string): ProjectDataState {
  const existing = projectDataStates.get(project);
  if (existing) {
    return existing;
  }
  const created: ProjectDataState = {};
  projectDataStates.set(project, created);
  return created;
}

function formatRevisionTimestamp(value?: number): string | undefined {
  if (!value || !Number.isFinite(value)) {
    return undefined;
  }
  return new Date(value).toISOString();
}

function readErrorName(error: unknown): string {
  if (error instanceof Error) {
    return error.name;
  }
  if (typeof error === "object" && error !== null && "name" in error) {
    return typeof error.name === "string" ? error.name : "";
  }
  return "";
}

function readErrorStatusCode(error: unknown): number | undefined {
  if (
    typeof error !== "object" ||
    error === null ||
    !("$metadata" in error) ||
    typeof error.$metadata !== "object" ||
    error.$metadata === null ||
    !("httpStatusCode" in error.$metadata)
  ) {
    return undefined;
  }
  return typeof error.$metadata.httpStatusCode === "number"
    ? error.$metadata.httpStatusCode
    : undefined;
}

function buildSummaryRevisionStatus(project: string): SummaryRevisionStatus {
  const state = getSummaryRevisionState(project);
  const inSync =
    !!state.loadedRevision &&
    !!state.s3Revision &&
    state.loadedRevision === state.s3Revision;
  let revisionLagMs = 0;
  if (!inSync && state.manifest?.publishedAt) {
    const publishedAtMs = new Date(state.manifest.publishedAt).getTime();
    if (!Number.isNaN(publishedAtMs)) {
      revisionLagMs = Math.max(0, Date.now() - publishedAtMs);
    }
  }
  return {
    hasManifest: state.hasManifest,
    inSync,
    s3Revision: state.s3Revision,
    loadedRevision: state.loadedRevision,
    lastCheckedAt: formatRevisionTimestamp(state.lastCheckedAtMs),
    lastLoadedAt: formatRevisionTimestamp(state.lastLoadedAtMs),
    publishedAt: state.manifest?.publishedAt,
    revisionLagMs,
    refreshDurationMs: state.refreshDurationMs,
  };
}

function computeProjectDataVersion(project: string): string {
  const summaryState = getSummaryRevisionState(project);
  const projectState = getProjectDataState(project);
  return JSON.stringify({
    loadedRevision: summaryState.loadedRevision ?? "",
    s3Revision: summaryState.s3Revision ?? "",
    lastRawSyncAtMs: projectState.lastRawSyncAtMs ?? 0,
    lastTableRefreshAtMs: projectState.lastTableRefreshAtMs ?? 0,
  });
}

function markProjectTableRefresh(project: string): void {
  const state = getProjectDataState(project);
  state.lastTableRefreshAtMs = Date.now();
  state.dataVersion = computeProjectDataVersion(project);
}

function markProjectRawSync(project: string): void {
  const state = getProjectDataState(project);
  state.lastRawSyncAtMs = Date.now();
}

function buildProjectDataStatus(project: string): ProjectDataStatus {
  const summary = buildSummaryRevisionStatus(project);
  const summaryState = getSummaryRevisionState(project);
  const projectState = getProjectDataState(project);
  const dataVersion =
    projectState.dataVersion ?? computeProjectDataVersion(project);

  return {
    ...summary,
    dataVersion,
    summaryRevision: summaryState.s3Revision,
    loadedSummaryRevision: summaryState.loadedRevision,
    lastRawSyncAt: formatRevisionTimestamp(projectState.lastRawSyncAtMs),
    lastTableRefreshAt: formatRevisionTimestamp(
      projectState.lastTableRefreshAtMs,
    ),
    rawSyncInFlight: syncInFlight.has(project),
    summarySyncInFlight: summaryRevisionInFlight.has(project),
  };
}

function isSummaryManifestFile(
  file: SummaryManifestFile | undefined,
): file is SummaryManifestFile {
  return (
    !!file &&
    typeof file.path === "string" &&
    file.path.length > 0 &&
    typeof file.sizeBytes === "number" &&
    Number.isFinite(file.sizeBytes) &&
    file.sizeBytes >= 0 &&
    typeof file.sha256 === "string" &&
    file.sha256.length > 0
  );
}

function parseSummaryManifest(value: string): SummaryManifest | undefined {
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed !== "object" || parsed === null) {
      return undefined;
    }
    const revision =
      "revision" in parsed && typeof parsed.revision === "string"
        ? parsed.revision
        : undefined;
    const publishedAt =
      "published_at" in parsed && typeof parsed.published_at === "string"
        ? parsed.published_at
        : undefined;
    const identities =
      "identities" in parsed &&
      typeof parsed.identities === "object" &&
      parsed.identities !== null
        ? parsed.identities
        : undefined;
    const trajectorySource =
      "trajectory_summary" in parsed &&
      typeof parsed.trajectory_summary === "object" &&
      parsed.trajectory_summary !== null
        ? parsed.trajectory_summary
        : identities &&
            typeof identities === "object" &&
            TRAJECTORY_SUMMARY_FILENAME in identities &&
            typeof identities[TRAJECTORY_SUMMARY_FILENAME] === "object" &&
            identities[TRAJECTORY_SUMMARY_FILENAME] !== null
          ? identities[TRAJECTORY_SUMMARY_FILENAME]
          : undefined;
    const evaluationSource =
      "evaluation_summary" in parsed &&
      typeof parsed.evaluation_summary === "object" &&
      parsed.evaluation_summary !== null
        ? parsed.evaluation_summary
        : identities &&
            typeof identities === "object" &&
            EVALUATION_SUMMARY_FILENAME in identities &&
            typeof identities[EVALUATION_SUMMARY_FILENAME] === "object" &&
            identities[EVALUATION_SUMMARY_FILENAME] !== null
          ? identities[EVALUATION_SUMMARY_FILENAME]
          : undefined;
    const trajectorySummary =
      trajectorySource && typeof trajectorySource === "object"
        ? {
            path:
              "path" in trajectorySource &&
              typeof trajectorySource.path === "string"
                ? trajectorySource.path
                : TRAJECTORY_SUMMARY_FILENAME,
            sizeBytes:
              "size_bytes" in trajectorySource &&
              typeof trajectorySource.size_bytes === "number"
                ? trajectorySource.size_bytes
                : "sizeBytes" in trajectorySource &&
                    typeof trajectorySource.sizeBytes === "number"
                  ? trajectorySource.sizeBytes
                  : -1,
            sha256:
              "sha256" in trajectorySource &&
              typeof trajectorySource.sha256 === "string"
                ? trajectorySource.sha256
                : "",
          }
        : undefined;
    const evaluationSummary =
      evaluationSource && typeof evaluationSource === "object"
        ? {
            path:
              "path" in evaluationSource &&
              typeof evaluationSource.path === "string"
                ? evaluationSource.path
                : EVALUATION_SUMMARY_FILENAME,
            sizeBytes:
              "size_bytes" in evaluationSource &&
              typeof evaluationSource.size_bytes === "number"
                ? evaluationSource.size_bytes
                : "sizeBytes" in evaluationSource &&
                    typeof evaluationSource.sizeBytes === "number"
                  ? evaluationSource.sizeBytes
                  : -1,
            sha256:
              "sha256" in evaluationSource &&
              typeof evaluationSource.sha256 === "string"
                ? evaluationSource.sha256
                : "",
          }
        : undefined;

    if (
      !revision ||
      !publishedAt ||
      !isSummaryManifestFile(trajectorySummary) ||
      !isSummaryManifestFile(evaluationSummary)
    ) {
      return undefined;
    }
    return {
      revision,
      publishedAt,
      trajectorySummary,
      evaluationSummary,
    };
  } catch {
    return undefined;
  }
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

function validateSummaryFilename(
  file: SummaryManifestFile,
  expectedName: string,
): string {
  const normalized = path.posix.basename(file.path);
  if (normalized !== expectedName) {
    throw new Error(
      `[db] Invalid summary manifest path: expected ${expectedName}, got ${file.path}`,
    );
  }
  return normalized;
}

async function fetchSummaryManifestFromS3(
  project: string,
): Promise<SummaryManifest | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }

  const state = getSummaryRevisionState(project);
  const key = summaryObjectKey(project, SUMMARY_MANIFEST_FILE);
  const bucket = getPrefix();

  try {
    const head = await getS3Client().send(
      new HeadObjectCommand({
        Bucket: bucket,
        Key: key,
      }),
    );
    const nextEtag =
      typeof head.ETag === "string" ? head.ETag.replaceAll('"', "") : undefined;
    if (
      state.manifest &&
      nextEtag &&
      state.manifestEtag &&
      nextEtag === state.manifestEtag
    ) {
      state.hasManifest = true;
      state.s3Revision = state.manifest.revision;
      return state.manifest;
    }

    const response = await getS3Client().send(
      new GetObjectCommand({
        Bucket: bucket,
        Key: key,
      }),
    );
    const body = await response.Body?.transformToString("utf-8");
    const manifest = body ? parseSummaryManifest(body) : undefined;
    if (!manifest) {
      throw new Error(`[db] Invalid summary manifest for ${project}`);
    }
    state.hasManifest = true;
    state.manifest = manifest;
    state.manifestEtag = nextEtag;
    state.s3Revision = manifest.revision;
    return manifest;
  } catch (error) {
    const name = readErrorName(error);
    const statusCode = readErrorStatusCode(error);
    if (name === "NoSuchKey" || name === "NotFound" || statusCode === 404) {
      state.hasManifest = false;
      state.manifest = undefined;
      state.manifestEtag = undefined;
      state.s3Revision = undefined;
      return undefined;
    }
    throw error;
  }
}

async function writeS3ObjectToPath(
  bucket: string,
  key: string,
  destPath: string,
): Promise<void> {
  const response = await getS3Client().send(
    new GetObjectCommand({
      Bucket: bucket,
      Key: key,
    }),
  );
  const bytes = await response.Body?.transformToByteArray();
  if (!bytes) {
    throw new Error(`[db] Missing S3 object body: ${key}`);
  }
  await writeFile(destPath, Buffer.from(bytes));
}

async function syncSummaryFilesToLocal(
  project: string,
  manifest: SummaryManifest,
): Promise<void> {
  const bucket = getPrefix();
  const summaryRoot = summaryDir(project);
  await mkdir(summaryRoot, { recursive: true });

  const trajectoryName = validateSummaryFilename(
    manifest.trajectorySummary,
    "trajectory_summary.parquet",
  );
  const evaluationName = validateSummaryFilename(
    manifest.evaluationSummary,
    "evaluation_summary.parquet",
  );

  const trajectoryDest = trajectorySummaryPath(project);
  const evaluationDest = evaluationSummaryPath(project);
  const manifestDest = summaryManifestPath(project);
  const trajectoryTemp = `${trajectoryDest}.tmp-${manifest.revision}`;
  const evaluationTemp = `${evaluationDest}.tmp-${manifest.revision}`;
  const manifestTemp = `${manifestDest}.tmp-${manifest.revision}`;

  await Promise.all([
    writeS3ObjectToPath(
      bucket,
      summaryObjectKey(project, trajectoryName),
      trajectoryTemp,
    ),
    writeS3ObjectToPath(
      bucket,
      summaryObjectKey(project, evaluationName),
      evaluationTemp,
    ),
  ]);

  await rename(trajectoryTemp, trajectoryDest);
  await rename(evaluationTemp, evaluationDest);
  await writeFile(
    manifestTemp,
    JSON.stringify(
      {
        revision: manifest.revision,
        published_at: manifest.publishedAt,
        trajectory_summary: {
          path: manifest.trajectorySummary.path,
          size_bytes: manifest.trajectorySummary.sizeBytes,
          sha256: manifest.trajectorySummary.sha256,
        },
        evaluation_summary: {
          path: manifest.evaluationSummary.path,
          size_bytes: manifest.evaluationSummary.sizeBytes,
          sha256: manifest.evaluationSummary.sha256,
        },
      },
      null,
      2,
    ),
  );
  await rename(manifestTemp, manifestDest);
}

async function readLocalSummaryManifest(
  project: string,
): Promise<SummaryManifest | undefined> {
  const manifestFile = summaryManifestPath(project);
  if (!(await pathExists(manifestFile))) {
    return undefined;
  }
  const body = await readFile(manifestFile, "utf-8");
  return parseSummaryManifest(body);
}

/** Run a single `aws s3 sync` command as a child process, returning a promise. */
function spawnS3Sync(
  source: string,
  dest: string,
  include: string,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "aws",
      ["s3", "sync", source, dest, "--exclude", "*", "--include", include],
      { stdio: ["ignore", "pipe", "pipe"], env: awsEnv() },
    );
    let stderr = "";
    child.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      const details = stderr.trim();
      reject(
        new Error(
          `[db] aws s3 sync failed include=${include} exitCode=${code ?? "unknown"}${details ? ` stderr=${details}` : ""}`,
        ),
      );
    });
    child.on("error", (error) => reject(error));
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
    logs?: boolean;
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
    tasks.push(spawnS3Sync(source, dest, `summaries/${SUMMARY_MANIFEST_FILE}`));
  }
  if (opts?.codeSnapshots !== false) {
    tasks.push(spawnS3Sync(source, dest, "*/code_snapshots.parquet"));
  }
  if (opts?.logs !== false) {
    tasks.push(spawnS3Sync(source, dest, "*/logs.parquet"));
  }
  if (tasks.length === 0) {
    return;
  }

  console.log(`[db] Background sync from ${source}`);
  await Promise.all(tasks);
  console.log(`[db] Background sync complete for ${project}`);
}

async function startRawTraceSync(
  project: string,
  force: boolean,
  options?: {
    refreshTables?: boolean;
    rethrowErrors?: boolean;
  },
): Promise<void> {
  const state = getProjectDataState(project);
  const now = Date.now();
  if (
    !force &&
    typeof state.lastRawSyncAtMs === "number" &&
    now - state.lastRawSyncAtMs < SYNC_MIN_INTERVAL_MS
  ) {
    console.log(
      `[db] Raw sync skip project=${project} reason=throttled ageMs=${now - state.lastRawSyncAtMs}`,
    );
    return;
  }

  const existing = rawSyncPromises.get(project);
  if (existing) {
    console.log(`[db] Raw sync join project=${project} force=${force}`);
    await existing;
    return;
  }

  syncInFlight.add(project);
  const startedAt = Date.now();
  const refreshTables = options?.refreshTables !== false;
  console.log(
    `[db] Raw sync start project=${project} force=${force} refreshTables=${refreshTables}`,
  );
  const task = (async () => {
    await syncFromS3(project, {
      summaries: false,
    });
    markProjectRawSync(project);
    lastSyncTime.set(project, Date.now());
    if (refreshTables) {
      const inst = await getDb(project);
      await refreshProjectTables(inst, project, { allowPartial: true });
      markProjectTableRefresh(project);
      clearCache();
    }
    console.log(
      `[db] Raw sync done project=${project} force=${force} refreshTables=${refreshTables} durationMs=${Date.now() - startedAt}`,
    );
  })().finally(() => {
    syncInFlight.delete(project);
    rawSyncPromises.delete(project);
  });

  rawSyncPromises.set(project, task);
  try {
    await task;
  } catch (error) {
    console.warn("[db] Background sync failed:", error);
    if (options?.rethrowErrors === true) {
      throw error;
    }
  }
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
    console.log(`[db] ensureSynced project=${project} localCache=hit`);
    if (!lastSyncTime.has(project)) {
      // Local cache exists — serve it immediately, sync in background.
      // No blocking sync needed: the table build will use existing files.
      lastSyncTime.set(project, Date.now());
      void startRawTraceSync(project, false);
      return;
    }
    // Data exists locally — serve it now, maybe sync in background
    const lastSync = lastSyncTime.get(project) ?? 0;
    if (
      syncInFlight.has(project) ||
      Date.now() - lastSync < SYNC_MIN_INTERVAL_MS
    ) {
      return;
    }
    void startRawTraceSync(project, false);
    return;
  }

  console.log(`[db] ensureSynced project=${project} localCache=miss`);

  // No local traces yet — sync summaries first for fast first paint.
  let hasSummaries = false;
  try {
    const manifest = await fetchSummaryManifestFromS3(project);
    if (manifest) {
      await syncSummaryFilesToLocal(project, manifest);
      hasSummaries = true;
    }
  } catch (error) {
    console.warn("[db] Failed to hydrate summaries from manifest:", error);
  }
  if (!hasSummaries) {
    await syncFromS3(project, {
      traces: false,
      summaries: true,
      codeSnapshots: false,
      logs: false,
    });
    hasSummaries = await hasLocalSummaryCache(project);
  }

  if (!hasSummaries) {
    // Fallback for older projects without summary parquet files.
    await syncFromS3(project, {
      traces: true,
      summaries: false,
      codeSnapshots: false,
      logs: false,
    });
    markProjectRawSync(project);
  }

  // Continue full sync in background so detail views stay local and fast.
  if (!syncInFlight.has(project)) {
    void startRawTraceSync(project, false);
  }
  lastSyncTime.set(project, Date.now());
}

async function refreshSummaryRevision(
  project: string,
): Promise<SummaryRevisionStatus> {
  const state = getSummaryRevisionState(project);
  state.lastCheckedAtMs = Date.now();

  const manifest = await fetchSummaryManifestFromS3(project);
  if (!manifest) {
    await syncFromS3(project, {
      traces: false,
      summaries: true,
      codeSnapshots: false,
      logs: false,
    });
    const inst = await getDb(project);
    await refreshProjectTables(inst, project, { allowPartial: true });
    markProjectTableRefresh(project);
    state.lastLoadedAtMs = Date.now();
    state.refreshDurationMs = undefined;
    clearCache();
    return buildSummaryRevisionStatus(project);
  }

  if (state.loadedRevision === manifest.revision) {
    return buildSummaryRevisionStatus(project);
  }

  const startedAt = Date.now();
  await syncSummaryFilesToLocal(project, manifest);
  const inst = await getDb(project);
  await refreshProjectTables(inst, project, { allowPartial: false });
  clearCache();
  state.loadedRevision = manifest.revision;
  state.lastLoadedAtMs = Date.now();
  state.refreshDurationMs = state.lastLoadedAtMs - startedAt;
  markProjectTableRefresh(project);
  return buildSummaryRevisionStatus(project);
}

async function ensureSummaryRevisionLoaded(
  project: string,
  opts?: { forceCheck?: boolean },
): Promise<SummaryRevisionStatus> {
  if (!isS3Configured()) {
    return buildSummaryRevisionStatus(project);
  }

  const state = getSummaryRevisionState(project);
  const now = Date.now();
  if (
    opts?.forceCheck !== true &&
    typeof state.lastCheckedAtMs === "number" &&
    now - state.lastCheckedAtMs < SUMMARY_REVISION_POLL_MS
  ) {
    return buildSummaryRevisionStatus(project);
  }

  const existing = summaryRevisionInFlight.get(project);
  if (existing) {
    return existing;
  }

  const next = refreshSummaryRevision(project)
    .catch((error) => {
      getSummaryRevisionState(project).lastCheckedAtMs = undefined;
      throw error;
    })
    .finally(() => {
      summaryRevisionInFlight.delete(project);
    });
  summaryRevisionInFlight.set(project, next);
  return next;
}

export async function getSummaryRevisionStatus(
  project?: string,
  opts?: { forceCheck?: boolean },
): Promise<SummaryRevisionStatus> {
  const activeProject = await getActiveProject(project);
  return ensureSummaryRevisionLoaded(activeProject, opts);
}

export async function ensureProjectDataFreshness(
  project?: string,
  opts?: { mode?: ProjectFreshnessMode },
): Promise<ProjectDataStatus> {
  const activeProject = await getActiveProject(project);
  const startedAt = Date.now();
  await getDb(activeProject);
  const mode = opts?.mode ?? "cached";
  console.log(
    `[db] ensureProjectDataFreshness project=${activeProject} mode=${mode}`,
  );
  if (mode === "force") {
    await startRawTraceSync(activeProject, true);
  } else if (mode === "fresh" && !syncInFlight.has(activeProject)) {
    void startRawTraceSync(activeProject, false);
  }
  console.log(
    `[db] ensureProjectDataFreshness done project=${activeProject} mode=${mode} durationMs=${Date.now() - startedAt}`,
  );
  return buildProjectDataStatus(activeProject);
}

export async function getProjectDataStatus(
  project?: string,
  opts?: { forceCheck?: boolean; mode?: ProjectFreshnessMode },
): Promise<ProjectDataStatus> {
  const activeProject = await getActiveProject(project);
  const startedAt = Date.now();
  await ensureProjectDataFreshness(activeProject, {
    mode: opts?.mode ?? "fresh",
  });
  if (opts?.forceCheck === true) {
    if ((opts?.mode ?? "fresh") === "force") {
      await ensureSummaryRevisionLoaded(activeProject, {
        forceCheck: true,
      });
    } else {
      void ensureSummaryRevisionLoaded(activeProject, {
        forceCheck: true,
      }).catch((error) => {
        console.warn(
          `[db] Summary revision refresh failed project=${activeProject}:`,
          formatError(error),
        );
      });
    }
  }
  console.log(
    `[db] getProjectDataStatus project=${activeProject} mode=${opts?.mode ?? "fresh"} forceCheck=${opts?.forceCheck === true} durationMs=${Date.now() - startedAt}`,
  );
  return buildProjectDataStatus(activeProject);
}

export function buildProjectDataHeaders(
  status: SummaryRevisionStatus | ProjectDataStatus,
): Record<string, string> {
  const dataVersion =
    "dataVersion" in status && typeof status.dataVersion === "string"
      ? status.dataVersion
      : "";
  return {
    "x-envoi-has-manifest": status.hasManifest ? "true" : "false",
    "x-envoi-in-sync": status.inSync ? "true" : "false",
    "x-envoi-s3-revision": status.s3Revision ?? "",
    "x-envoi-loaded-revision": status.loadedRevision ?? "",
    "x-envoi-data-version": dataVersion,
  };
}

export const buildSummaryRevisionHeaders = buildProjectDataHeaders;

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

/** URI for logs.parquet — uses local cache if available, else S3. */
export async function logsUri(
  trajectoryId: string,
  project?: string,
): Promise<string> {
  const activeProject = await getActiveProject(project);
  const validId = validateTrajectoryId(trajectoryId);
  const localPath = path.join(cacheDir(activeProject), validId, "logs.parquet");
  if (await pathExists(localPath)) {
    return localPath;
  }
  return `s3://${getPrefix()}/project/${activeProject}/trajectories/${validId}/logs.parquet`;
}

/** Always return S3 URI for logs.parquet, bypassing local cache. */
export async function freshLogsUri(
  trajectoryId: string,
  project?: string,
): Promise<string> {
  const activeProject = await getActiveProject(project);
  const validId = validateTrajectoryId(trajectoryId);
  return `s3://${getPrefix()}/project/${activeProject}/trajectories/${validId}/logs.parquet`;
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
      suites VARCHAR,
      sandbox_id VARCHAR,
      sandbox_provider VARCHAR,
      best_passed INTEGER,
      best_failed INTEGER,
      best_total INTEGER,
      eval_count INTEGER
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

type RebuildTraceOptions = {
  evaluationsTable?: string;
  loadEvalScores?: boolean;
};

async function rebuildTrajectoriesFromTraceFiles(
  project: string,
  targetTable: string,
  conn: Awaited<ReturnType<DuckDBInstance["connect"]>>,
  options?: RebuildTraceOptions,
): Promise<void> {
  const files = await listTraceFiles(project);
  const evaluationsTable = options?.evaluationsTable;
  const loadEvalScores = options?.loadEvalScores !== false;
  await conn.run(createTrajectoriesSchemaSql(targetTable));
  if (evaluationsTable) {
    await conn.run(createEvaluationsSchemaSql(evaluationsTable));
  }
  for (const file of files) {
    const logsFile = path.join(path.dirname(file), "logs.parquet");
    const hasLogs = await pathExists(logsFile);
    // Detect available columns — older parquet files may lack sandbox_id etc.
    const colResult = await conn.run(
      `SELECT name FROM parquet_schema('${file}')`,
    );
    const colRows = await colResult.getRowObjectsJson();
    const columns = new Set(colRows.map((row) => String(row.name)));
    const hasSandboxId = columns.has("sandbox_id");
    const hasSandboxProvider = columns.has("sandbox_provider");

    const traceSummaryResult = await conn.run(`
      SELECT
        trajectory_id,
        environment,
        agent_model,
        MIN(agent) AS agent,
        MIN(started_at) AS started_at,
        MAX(timestamp) AS trace_ended_at,
        MAX(part) + 1 AS total_parts,
        MAX(turn) AS total_turns,
        SUM(content_token_estimate) AS total_tokens,
        MAX(session_end_reason) AS session_end_reason,
        MIN(task_params) AS task_params,
        arg_max(suites, part) AS suites,
        ${hasSandboxId ? "arg_max(sandbox_id, part)" : "NULL"} AS sandbox_id,
        ${hasSandboxProvider ? "arg_max(sandbox_provider, part)" : "NULL"} AS sandbox_provider
      FROM read_parquet('${file}')
      GROUP BY trajectory_id, environment, agent_model
    `);
    const traceSummaryRows = await traceSummaryResult.getRowObjectsJson();
    const traceSummaryRow = traceSummaryRows[0];
    if (!traceSummaryRow) {
      continue;
    }

    let endedAt = String(traceSummaryRow.trace_ended_at ?? "");
    if (hasLogs) {
      const logSummaryResult = await conn.run(`
        SELECT MAX(ts) AS log_ended_at
        FROM read_parquet('${logsFile}')
      `);
      const logSummaryRows = await logSummaryResult.getRowObjectsJson();
      const logEndedAt = logSummaryRows[0]?.log_ended_at;
      if (logEndedAt !== undefined && String(logEndedAt) > endedAt) {
        endedAt = String(logEndedAt);
      }
    }
    // Compute best eval scores and populate evaluations table by streaming
    // eval rows one at a time. The full eval_events_delta column can be
    // 100-300 MB, exceeding the 960MB DuckDB limit. We get part numbers first,
    // then read each row individually (parquet predicate pushdown).
    // Skip entirely if the parquet lacks eval_events_delta (older format).
    const hasEvalColumn = columns.has("eval_events_delta");
    const trajectoryId = String(traceSummaryRow.trajectory_id ?? "");
    const environment = String(traceSummaryRow.environment ?? "");
    const agentModel = String(traceSummaryRow.agent_model ?? "");
    let bestPassed = 0;
    let bestFailed = 0;
    let bestTotal = 0;
    let evalCount = 0;
    if (hasEvalColumn && loadEvalScores) {
      try {
        const partsResult = await conn.run(`
          SELECT part
          FROM read_parquet('${file}')
          WHERE eval_events_delta IS NOT NULL
            AND LENGTH(CAST(eval_events_delta AS VARCHAR)) > 2
          ORDER BY part
        `);
        const partsRows = await partsResult.getRowObjectsJson();

        for (const partRow of partsRows) {
          const partNum = Number(partRow.part);
          const rowResult = await conn.run(`
            SELECT eval_events_delta, turn
            FROM read_parquet('${file}')
            WHERE part = ${partNum}
            LIMIT 1
          `);
          const rowData = await rowResult.getRowObjectsJson();
          const rawEvents = rowData[0]?.eval_events_delta;
          const turn =
            rowData[0]?.turn != undefined ? Number(rowData[0].turn) : undefined;
          if (typeof rawEvents !== "string" || rawEvents.length <= 2) {
            continue;
          }
          try {
            const events = JSON.parse(rawEvents);
            if (!Array.isArray(events)) {
              continue;
            }
            for (const event of events) {
              if (typeof event !== "object" || event === null) {
                continue;
              }
              const status = "status" in event ? String(event.status) : "";
              const passed =
                "passed" in event && typeof event.passed === "number"
                  ? event.passed
                  : 0;
              const failed =
                "failed" in event && typeof event.failed === "number"
                  ? event.failed
                  : 0;
              const total =
                "total" in event && typeof event.total === "number"
                  ? event.total
                  : 0;
              const evalId =
                "eval_id" in event && typeof event.eval_id === "string"
                  ? event.eval_id
                  : "";
              const targetCommit =
                "target_commit" in event &&
                typeof event.target_commit === "string"
                  ? event.target_commit
                  : "";
              const suiteResults =
                "suite_results" in event
                  ? JSON.stringify(event.suite_results ?? {})
                  : "{}";
              const finishedAt =
                "finished_at" in event && typeof event.finished_at === "string"
                  ? event.finished_at
                  : undefined;

              if (evaluationsTable && evalId) {
                await conn.run(`
                  INSERT INTO ${evaluationsTable} VALUES (
                    '${sqlLiteral(trajectoryId)}',
                    '${sqlLiteral(environment)}',
                    '${sqlLiteral(agentModel)}',
                    ${partNum},
                    ${turn ?? "NULL"},
                    '${sqlLiteral(targetCommit)}',
                    '${sqlLiteral(evalId)}',
                    '${sqlLiteral(status)}',
                    ${passed},
                    ${failed},
                    ${total},
                    '${sqlLiteral(targetCommit)}',
                    '${sqlLiteral(suiteResults)}',
                    ${finishedAt ? `'${sqlLiteral(finishedAt)}'` : "NULL"}
                  )
                `);
              }

              if (status === "completed") {
                evalCount++;
                if (passed > bestPassed || total > bestTotal) {
                  bestPassed = passed;
                  bestFailed = failed;
                  bestTotal = total;
                }
              }
            }
          } catch {
            continue;
          }
        }
      } catch (scoreError) {
        console.warn(
          `[db] eval score query failed for ${file}:`,
          formatError(scoreError),
        );
      }
    }

    const values = {
      trajectory_id: String(traceSummaryRow.trajectory_id ?? ""),
      environment: String(traceSummaryRow.environment ?? ""),
      agent_model: String(traceSummaryRow.agent_model ?? ""),
      agent: String(traceSummaryRow.agent ?? ""),
      started_at: String(traceSummaryRow.started_at ?? ""),
      ended_at: endedAt,
      total_parts: Number(traceSummaryRow.total_parts ?? 0),
      total_turns: Number(traceSummaryRow.total_turns ?? 0),
      total_tokens: Number(traceSummaryRow.total_tokens ?? 0),
      session_end_reason:
        traceSummaryRow.session_end_reason != undefined
          ? String(traceSummaryRow.session_end_reason)
          : "",
      task_params:
        traceSummaryRow.task_params != undefined
          ? String(traceSummaryRow.task_params)
          : "",
      suites:
        traceSummaryRow.suites != undefined
          ? String(traceSummaryRow.suites)
          : "",
      sandbox_id:
        traceSummaryRow.sandbox_id != undefined
          ? String(traceSummaryRow.sandbox_id)
          : "",
      sandbox_provider:
        traceSummaryRow.sandbox_provider != undefined
          ? String(traceSummaryRow.sandbox_provider)
          : "",
    };

    await conn.run(`
      INSERT INTO ${targetTable} VALUES (
        '${sqlLiteral(values.trajectory_id)}',
        '${sqlLiteral(values.environment)}',
        '${sqlLiteral(values.agent_model)}',
        '${sqlLiteral(values.agent)}',
        '${sqlLiteral(values.started_at)}',
        '${sqlLiteral(values.ended_at)}',
        ${values.total_parts},
        ${values.total_turns},
        ${values.total_tokens},
        ${values.session_end_reason ? `'${sqlLiteral(values.session_end_reason)}'` : "NULL"},
        ${values.task_params ? `'${sqlLiteral(values.task_params)}'` : "NULL"},
        ${values.suites ? `'${sqlLiteral(values.suites)}'` : "NULL"},
        ${values.sandbox_id ? `'${sqlLiteral(values.sandbox_id)}'` : "NULL"},
        ${values.sandbox_provider ? `'${sqlLiteral(values.sandbox_provider)}'` : "NULL"},
        ${bestPassed > 0 ? bestPassed : "NULL"},
        ${bestFailed > 0 ? bestFailed : "NULL"},
        ${bestTotal > 0 ? bestTotal : "NULL"},
        ${evalCount}
      )
    `);
  }
}

async function createAnalyticsViews(
  inst: DuckDBInstance,
  project: string,
): Promise<void> {
  // Serialize per-project to prevent concurrent table rebuilds from racing
  // on shared temp table names (trajectories_next, evaluations_next).
  const existing = analyticsViewsLock.get(project);
  if (existing) {
    await existing;
  }
  let releaseLock = () => {};
  const lockPromise = new Promise<void>((res) => {
    releaseLock = res;
  });
  analyticsViewsLock.set(project, lockPromise);
  try {
    await createAnalyticsViewsInner(inst, project);
  } finally {
    analyticsViewsLock.delete(project);
    releaseLock();
  }
}

async function createAnalyticsViewsInner(
  inst: DuckDBInstance,
  project: string,
): Promise<void> {
  const startedAt = Date.now();
  const conn = await inst.connect();
  const refreshToken = `${Date.now()}_${Math.random()
    .toString(36)
    .slice(2, 10)}`;
  const nextTrajectoriesTable = `trajectories_next_${refreshToken}`;
  const nextEvaluationsTable = `evaluations_next_${refreshToken}`;
  const [hasTrajectorySummary, hasEvaluationSummary] = await Promise.all([
    pathExists(trajectorySummaryPath(project)),
    pathExists(evaluationSummaryPath(project)),
  ]);
  const hasRawTraces = await hasLocalCache(project);
  try {
    await conn.run("SET threads=1");
    await conn.run("SET preserve_insertion_order=false");

    await conn.run("DROP VIEW IF EXISTS turn_summaries");
    await conn.run("DROP VIEW IF EXISTS file_access");
    await conn.run(`DROP TABLE IF EXISTS ${nextTrajectoriesTable}`);
    await conn.run(`DROP TABLE IF EXISTS ${nextEvaluationsTable}`);

    // Build trajectories from both summary and raw traces, then merge.
    // Raw traces are fresher (have live data for active runs) but may not
    // include all trajectories if S3 sync is still in progress. The summary
    // has the complete set from S3. Merge: raw wins when both exist.
    const rawTable = `${nextTrajectoriesTable}_raw`;
    const summaryTable = `${nextTrajectoriesTable}_summary`;
    await conn.run(`DROP TABLE IF EXISTS ${rawTable}`);
    await conn.run(`DROP TABLE IF EXISTS ${summaryTable}`);

    // When no evaluation summary exists, populate evaluations from raw traces.
    const evalsTarget = hasEvaluationSummary ? undefined : nextEvaluationsTable;
    const loadEvalScores = !hasEvaluationSummary;

    if (hasRawTraces && hasTrajectorySummary) {
      const summaryPath = sqlLiteral(trajectorySummaryPath(project));
      await rebuildTrajectoriesFromTraceFiles(project, rawTable, conn, {
        evaluationsTable: evalsTarget,
        loadEvalScores,
      });
      await conn.run(`
        CREATE TABLE ${summaryTable} AS
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
          suites,
          NULL::VARCHAR AS sandbox_id,
          NULL::VARCHAR AS sandbox_provider,
          0 AS best_passed,
          0 AS best_failed,
          0 AS best_total,
          0 AS eval_count
        FROM read_parquet('${summaryPath}')
      `);
      await conn.run(`
        CREATE TABLE ${nextTrajectoriesTable} AS
        SELECT * FROM ${rawTable}
        UNION ALL
        SELECT * FROM ${summaryTable}
        WHERE trajectory_id NOT IN (SELECT trajectory_id FROM ${rawTable})
      `);
      await conn.run(`DROP TABLE IF EXISTS ${rawTable}`);
      await conn.run(`DROP TABLE IF EXISTS ${summaryTable}`);
    } else if (hasRawTraces) {
      await rebuildTrajectoriesFromTraceFiles(
        project,
        nextTrajectoriesTable,
        conn,
        {
          evaluationsTable: evalsTarget,
          loadEvalScores,
        },
      );
    } else if (hasTrajectorySummary) {
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
          suites,
          NULL::VARCHAR AS sandbox_id,
          NULL::VARCHAR AS sandbox_provider,
          0 AS best_passed,
          0 AS best_failed,
          0 AS best_total,
          0 AS eval_count
        FROM read_parquet('${summaryPath}')
      `);
    } else {
      await rebuildTrajectoriesFromTraceFiles(
        project,
        nextTrajectoriesTable,
        conn,
        {
          evaluationsTable: evalsTarget,
          loadEvalScores,
        },
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
    } else if (!evalsTarget) {
      // Only create empty evaluations if rebuild didn't populate it
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
    console.log(
      `[db] createAnalyticsViews project=${project} trajectoriesSource=${hasRawTraces ? "raw" : hasTrajectorySummary ? "summary" : "empty"} evaluationsSource=${hasEvaluationSummary ? "summary" : "empty"} durationMs=${Date.now() - startedAt}`,
    );
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
    { name: "summary tables", run: () => loadSummaryTables(inst, project) },
    {
      name: "analytics tables",
      run: () => createAnalyticsViews(inst, project),
    },
  ] as const;
  const failures: Array<{ name: string; reason: unknown }> = [];

  for (const task of tasks) {
    try {
      await task.run();
    } catch (error) {
      failures.push({ name: task.name, reason: error });
    }
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
    const analyticsFailure = failures.find(
      (failure) => failure.name === "analytics tables",
    );
    if (!analyticsFailure) {
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

async function configureInstance(inst: DuckDBInstance): Promise<void> {
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
      await conn.run("SET unsafe_disable_etag_checks=true");
    } finally {
      conn.disconnectSync();
    }
  }
}

async function createInstance(project: string): Promise<DuckDBInstance> {
  await Promise.all([ensureDbDir(project), ensureSynced(project)]);
  const projectDbPath = dbPath(project);

  // Try to reuse an existing DB file to avoid the ~3s table rebuild on restart.
  // If the DB has a trajectories table with rows, it's usable as-is.
  // Background sync will refresh tables when new data arrives.
  if (await pathExists(projectDbPath)) {
    try {
      const inst = await DuckDBInstance.create(projectDbPath);
      await configureInstance(inst);
      const conn = await inst.connect();
      try {
        const result = await conn.run(
          "SELECT COUNT(*) AS n FROM trajectories LIMIT 1",
        );
        const rows = await result.getRowObjectsJson();
        if (rows.length > 0 && Number(rows[0]?.n) > 0) {
          console.log(
            `[db] createInstance project=${project} reused existing DB`,
          );
          markProjectTableRefresh(project);
          // Refresh tables in background so data updates eventually.
          void refreshProjectTables(inst, project, {
            allowPartial: true,
          }).then(() => {
            markProjectTableRefresh(project);
            clearCache();
          });
          return inst;
        }
      } finally {
        conn.disconnectSync();
      }
    } catch {
      // DB is corrupt or incompatible — fall through to fresh rebuild.
    }
  }

  // Fresh rebuild: delete stale DB + WAL, create from scratch.
  await Promise.all([
    rm(projectDbPath, { force: true }),
    rm(`${projectDbPath}.wal`, { force: true }),
  ]);

  const inst = await DuckDBInstance.create(projectDbPath);
  await configureInstance(inst);

  await refreshProjectTables(inst, project, { allowPartial: true });
  markProjectTableRefresh(project);
  const localManifest = await readLocalSummaryManifest(project);
  if (localManifest) {
    const state = getSummaryRevisionState(project);
    state.hasManifest = true;
    state.manifest = localManifest;
    state.s3Revision = localManifest.revision;
    state.loadedRevision = localManifest.revision;
    state.lastLoadedAtMs = Date.now();
    markProjectTableRefresh(project);
  }
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
  rawSyncPromises.delete(activeProject);
  await startRawTraceSync(activeProject, true);
  await ensureSummaryRevisionLoaded(activeProject, { forceCheck: true });
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
