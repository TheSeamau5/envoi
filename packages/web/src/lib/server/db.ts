/**
 * DuckDB server-side singleton module.
 *
 * On startup, syncs parquet files from S3 to a local cache directory so that
 * DuckDB queries run against local disk instead of over the network.
 * Must only be imported from server-side code (API routes, server components).
 */

import { DuckDBInstance } from "@duckdb/node-api";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";

let instance: DuckDBInstance | undefined;
let initPromise: Promise<DuckDBInstance> | undefined;

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
// Local parquet cache
// ---------------------------------------------------------------------------

const CACHE_DIR = path.resolve(process.cwd(), ".cache", "parquet", "trajectories");

/** Ensure the local cache directory exists */
function ensureCacheDir(): void {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
}

/** Sync parquet files from S3 to local cache (blocking, runs once at startup) */
function syncFromS3(): void {
  if (!isS3Configured()) return;

  ensureCacheDir();
  const bucket = getBucket();
  const s3Path = `s3://${bucket}/trajectories/`;

  try {
    console.log("[db] Syncing parquet files from S3...");
    execSync(
      `aws s3 sync "${s3Path}" "${CACHE_DIR}" --exclude "*" --include "*/trace.parquet"`,
      {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 60_000,
        env: {
          ...process.env,
          AWS_ACCESS_KEY_ID: process.env.AWS_ACCESS_KEY_ID,
          AWS_SECRET_ACCESS_KEY: process.env.AWS_SECRET_ACCESS_KEY,
          AWS_REGION: process.env.AWS_REGION ?? "us-east-1",
        },
      },
    );
    console.log("[db] S3 sync complete.");
  } catch (err) {
    console.warn("[db] S3 sync failed, will query S3 directly:", err);
  }
}

/** Whether local cache has parquet files */
function hasLocalCache(): boolean {
  try {
    const dirs = fs.readdirSync(CACHE_DIR);
    return dirs.some((d) =>
      fs.existsSync(path.join(CACHE_DIR, d, "trace.parquet")),
    );
  } catch {
    return false;
  }
}

// Run sync eagerly when this module is first imported
let synced = false;
function ensureSynced(): void {
  if (synced) return;
  synced = true;
  syncFromS3();
}

// ---------------------------------------------------------------------------
// URI helpers — point to local cache when available, S3 as fallback
// ---------------------------------------------------------------------------

/** Glob for all trace parquet files */
export function allTracesGlob(): string {
  ensureSynced();
  if (hasLocalCache()) {
    return path.join(CACHE_DIR, "*", "trace.parquet");
  }
  return `s3://${getBucket()}/trajectories/*/trace.parquet`;
}

/** URI for a single trajectory's parquet file */
export function traceUri(trajectoryId: string): string {
  ensureSynced();
  const localPath = path.join(CACHE_DIR, trajectoryId, "trace.parquet");
  if (fs.existsSync(localPath)) {
    return localPath;
  }
  return `s3://${getBucket()}/trajectories/${trajectoryId}/trace.parquet`;
}

// ---------------------------------------------------------------------------
// DuckDB instance
// ---------------------------------------------------------------------------

async function createInstance(): Promise<DuckDBInstance> {
  const inst = await DuckDBInstance.create();

  // Only configure httpfs if we'll need S3 access (no local cache)
  if (isS3Configured() && !hasLocalCache()) {
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

  return inst;
}

/** Get the shared DuckDB instance (creates on first call) */
export async function getDb(): Promise<DuckDBInstance> {
  if (instance) return instance;
  if (!initPromise) {
    initPromise = createInstance().then((inst) => {
      instance = inst;
      return inst;
    });
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
