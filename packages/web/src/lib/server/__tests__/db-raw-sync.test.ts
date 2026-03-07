import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EventEmitter } from "node:events";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";

const clearCacheMock = vi.fn();
const duckRunMock = vi.fn(async (...args: [string?]) => {
  void args;
  return {
    getRowObjectsJson: async () => [],
  };
});
const duckDisconnectMock = vi.fn();
const duckConnectMock = vi.fn(async () => ({
  run: duckRunMock,
  disconnectSync: duckDisconnectMock,
}));
const duckCreateMock = vi.fn(async () => ({
  connect: duckConnectMock,
}));
const spawnMock = vi.fn();

vi.mock("@duckdb/node-api", () => ({
  DuckDBInstance: {
    create: duckCreateMock,
  },
}));

vi.mock("@aws-sdk/client-s3", () => {
  class S3Client {}

  class GetObjectCommand {
    input: Record<string, unknown>;

    constructor(input: Record<string, unknown>) {
      this.input = input;
    }
  }

  class HeadObjectCommand {
    input: Record<string, unknown>;

    constructor(input: Record<string, unknown>) {
      this.input = input;
    }
  }

  class PutObjectCommand {
    input: Record<string, unknown>;

    constructor(input: Record<string, unknown>) {
      this.input = input;
    }
  }

  return {
    S3Client,
    GetObjectCommand,
    HeadObjectCommand,
    PutObjectCommand,
  };
});

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => undefined,
  }),
}));

vi.mock("../cache", () => ({
  clearCache: clearCacheMock,
}));

vi.mock("node:child_process", () => ({
  spawn: spawnMock,
}));

type FakeChild = EventEmitter & {
  stderr: EventEmitter;
  stdout: EventEmitter;
};

async function writeTraceFile(
  root: string,
  project: string,
  trajectoryId: string,
): Promise<void> {
  const dir = path.join(
    root,
    ".cache",
    "parquet",
    project,
    "trajectories",
    trajectoryId,
  );
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, "trace.parquet"), "stub");
}

function makeChild(): FakeChild {
  const child = new EventEmitter() as FakeChild;
  child.stderr = new EventEmitter();
  child.stdout = new EventEmitter();
  return child;
}

describe("raw trace sync startup behavior", () => {
  const project = "c-compiler";
  const originalCwd = process.cwd();
  let tempRoot = "";

  beforeEach(async () => {
    process.env.AWS_S3_PREFIX = "test-bucket";
    process.env.AWS_REGION = "us-east-1";
    tempRoot = path.join(
      "/tmp",
      `db-raw-sync-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    );
    spawnMock.mockReset();
    duckRunMock.mockClear();
    duckCreateMock.mockClear();
    clearCacheMock.mockClear();
    // Clear globalThis singletons so each test starts fresh
    const g = globalThis as Record<string, unknown>;
    for (const key of Object.keys(g)) {
      if (key.startsWith("envoi")) {
        delete g[key];
      }
    }
    vi.resetModules();
    await rm(tempRoot, { recursive: true, force: true });
    await mkdir(tempRoot, { recursive: true });
  });

  afterEach(async () => {
    process.chdir(originalCwd);
    await rm(tempRoot, { recursive: true, force: true });
    vi.resetModules();
  });

  it("serves existing local traces immediately without blocking on sync", async () => {
    await writeTraceFile(tempRoot, project, "traj-a");
    await writeTraceFile(tempRoot, project, "traj-c");

    spawnMock.mockImplementation(() => {
      const child = makeChild();
      setTimeout(() => {
        child.emit("close", 0);
      }, 25);
      return child;
    });

    process.chdir(tempRoot);
    const db = await import("../db");
    await db.getDb(project);

    // The two existing trace files should be loaded (traj-a and traj-c)
    const traceReads = duckRunMock.mock.calls
      .map((call) => String(call[0] ?? ""))
      .filter(
        (sql) =>
          sql.includes("read_parquet('") && sql.includes("trace.parquet"),
      );

    expect(traceReads.some((sql) => sql.includes("traj-a/trace.parquet"))).toBe(
      true,
    );
    expect(traceReads.some((sql) => sql.includes("traj-c/trace.parquet"))).toBe(
      true,
    );
  });

  it("succeeds even when background aws s3 sync fails", async () => {
    await writeTraceFile(tempRoot, project, "traj-a");
    await writeTraceFile(tempRoot, project, "traj-c");

    spawnMock.mockImplementation(() => {
      const child = makeChild();
      setTimeout(() => {
        child.stderr.emit("data", Buffer.from("simulated sync failure"));
        child.emit("close", 1);
      }, 10);
      return child;
    });

    process.chdir(tempRoot);
    const db = await import("../db");

    // getDb should succeed using local cache even when sync fails in background
    const inst = await db.getDb(project);
    expect(inst).toBeDefined();
    expect(duckCreateMock).toHaveBeenCalled();
  });
});
