/**
 * DuckDB server-side singleton module.
 *
 * Provides a shared DuckDB instance with httpfs configured for S3 access.
 * Must only be imported from server-side code (API routes, server components).
 */

import { DuckDBInstance } from "@duckdb/node-api";

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

/** S3 URI for all trace parquet files */
export function allTracesGlob(): string {
  return `s3://${getBucket()}/trajectories/*/trace.parquet`;
}

/** S3 URI for a single trajectory's parquet file */
export function traceUri(trajectoryId: string): string {
  return `s3://${getBucket()}/trajectories/${trajectoryId}/trace.parquet`;
}

async function createInstance(): Promise<DuckDBInstance> {
  const inst = await DuckDBInstance.create();
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
    // @duckdb/node-api only provides sync disconnect — no async equivalent
    conn.disconnectSync();
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
    // @duckdb/node-api only provides sync disconnect — no async equivalent
    conn.disconnectSync();
  }
}
