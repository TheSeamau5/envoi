import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { rm } from "node:fs/promises";
import path from "node:path";

const clearCacheMock = vi.fn();
const duckRunMock = vi.fn(async () => ({
  getRowObjectsJson: async () => [],
}));
const duckDisconnectMock = vi.fn();
const duckConnectMock = vi.fn(async () => ({
  run: duckRunMock,
  disconnectSync: duckDisconnectMock,
}));
const duckCreateMock = vi.fn(async () => ({
  connect: duckConnectMock,
}));

type RemoteState = {
  manifestEtag: string;
  manifestBody: string;
  objects: Record<string, Uint8Array>;
};

const remoteState: RemoteState = {
  manifestEtag: "etag-0",
  manifestBody: "",
  objects: {},
};

const sendMock = vi.fn(async (command: { input?: Record<string, unknown> }) => {
  const key =
    command.input && typeof command.input.Key === "string"
      ? command.input.Key
      : "";
  if (key.endsWith("/manifest.json")) {
    if (command.constructor.name === "HeadObjectCommand") {
      return {
        ETag: `"${remoteState.manifestEtag}"`,
      };
    }
    return {
      Body: {
        transformToString: async () => remoteState.manifestBody,
      },
    };
  }

  const objectBody = remoteState.objects[key];
  if (!objectBody) {
    throw new Error(`Missing object for ${key}`);
  }
  return {
    Body: {
      transformToByteArray: async () => objectBody,
    },
  };
});

vi.mock("@duckdb/node-api", () => ({
  DuckDBInstance: {
    create: duckCreateMock,
  },
}));

vi.mock("@aws-sdk/client-s3", () => {
  class S3Client {
    send = sendMock;
  }

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

function makeManifest(revision: string) {
  return {
    revision,
    published_at: "2026-01-01T00:00:00Z",
    identities: {
      "trajectory_summary.parquet": {
        sha256: `traj-${revision}`,
        size_bytes: 4,
      },
      "evaluation_summary.parquet": {
        sha256: `eval-${revision}`,
        size_bytes: 4,
      },
    },
  };
}

function setRemoteRevision(project: string, revision: string, etag: string): void {
  const manifest = makeManifest(revision);
  remoteState.manifestEtag = etag;
  remoteState.manifestBody = JSON.stringify(manifest);
  remoteState.objects = {
    [`project/${project}/trajectories/summaries/trajectory_summary.parquet`]:
      Uint8Array.from([1, 2, 3, 4]),
    [`project/${project}/trajectories/summaries/evaluation_summary.parquet`]:
      Uint8Array.from([5, 6, 7, 8]),
  };
}

async function cleanupProject(project: string): Promise<void> {
  await Promise.all([
    rm(
      path.resolve(
        process.cwd(),
        ".cache",
        "parquet",
        project,
      ),
      { recursive: true, force: true },
    ),
    rm(
      path.resolve(process.cwd(), ".cache", "duckdb", `${project}.duckdb`),
      { force: true },
    ),
    rm(
      path.resolve(process.cwd(), ".cache", "duckdb", `${project}.duckdb.wal`),
      { force: true },
    ),
  ]);
}

describe("summary revision loading", () => {
  const project = "revision-test";

  beforeEach(async () => {
    process.env.AWS_S3_PREFIX = "test-bucket";
    process.env.AWS_REGION = "us-east-1";
    sendMock.mockClear();
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
    await cleanupProject(project);
  });

  afterEach(async () => {
    await cleanupProject(project);
    vi.resetModules();
  });

  it("does not reload summary tables when the manifest revision is unchanged", async () => {
    setRemoteRevision(project, "rev-1", "etag-1");
    const db = await import("../db");

    const first = await db.getSummaryRevisionStatus(project, {
      forceCheck: true,
    });
    expect(first.loadedRevision).toBe("rev-1");
    expect(first.inSync).toBe(true);

    const firstRunCount = duckRunMock.mock.calls.length;
    const firstClearCount = clearCacheMock.mock.calls.length;

    sendMock.mockClear();
    const second = await db.getSummaryRevisionStatus(project, {
      forceCheck: true,
    });

    expect(second.loadedRevision).toBe("rev-1");
    expect(second.inSync).toBe(true);
    expect(duckRunMock.mock.calls.length).toBe(firstRunCount);
    expect(clearCacheMock.mock.calls.length).toBe(firstClearCount);
    expect(sendMock.mock.calls).toHaveLength(1);
    expect(sendMock.mock.calls[0]?.[0]?.constructor.name).toBe("HeadObjectCommand");
  });

  it("reloads summary tables exactly once when a newer manifest revision appears", async () => {
    setRemoteRevision(project, "rev-1", "etag-1");
    const db = await import("../db");

    const first = await db.getSummaryRevisionStatus(project, {
      forceCheck: true,
    });
    expect(first.loadedRevision).toBe("rev-1");

    const firstRunCount = duckRunMock.mock.calls.length;
    const firstClearCount = clearCacheMock.mock.calls.length;

    setRemoteRevision(project, "rev-2", "etag-2");
    const second = await db.getSummaryRevisionStatus(project, {
      forceCheck: true,
    });

    expect(second.s3Revision).toBe("rev-2");
    expect(second.loadedRevision).toBe("rev-2");
    expect(second.inSync).toBe(true);
    expect(duckRunMock.mock.calls.length).toBeGreaterThan(firstRunCount);
    expect(clearCacheMock.mock.calls.length).toBe(firstClearCount + 1);
  });

  it("reports holistic project data status with a data version", async () => {
    setRemoteRevision(project, "rev-1", "etag-1");
    const db = await import("../db");

    const status = await db.getProjectDataStatus(project, {
      forceCheck: true,
      mode: "cached",
    });

    expect(status.hasManifest).toBe(true);
    expect(status.loadedRevision).toBe("rev-1");
    expect(status.loadedSummaryRevision).toBe("rev-1");
    expect(typeof status.dataVersion).toBe("string");
    expect(status.dataVersion.length).toBeGreaterThan(0);
    expect(typeof status.summarySyncInFlight).toBe("boolean");
  });

  it("includes the data version in project freshness headers", async () => {
    const db = await import("../db");

    const headers = db.buildProjectDataHeaders({
      hasManifest: false,
      inSync: false,
      revisionLagMs: 0,
      dataVersion: "version-123",
      rawSyncInFlight: false,
      summarySyncInFlight: false,
    });

    expect(headers["x-envoi-data-version"]).toBe("version-123");
  });
});
