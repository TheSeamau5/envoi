import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import type { Trajectory } from "@/lib/types";

const getProjectFromRequestMock = vi.fn(async () => "c-compiler");
const getProjectsForUiMock = vi.fn(async () => [
  {
    name: "c-compiler",
    description: "Compiler project",
    createdAt: "2026-03-01T00:00:00.000Z",
    updatedAt: "2026-03-08T00:00:00.000Z",
    trajectoryCount: 2,
    environmentCount: 1,
    modelCount: 1,
  },
]);
const getTrajectoryDetailFromSnapshotMock = vi.fn(
  async (_project: string, trajectoryId: string) =>
    ({
      id: trajectoryId,
      model: "codex/gpt-5.3-codex",
      environment: "c_compiler",
      commits: [],
      totalTests: 10,
      startedAt: "2026-03-08T00:00:00.000Z",
      duration: "5m",
      totalTokens: 100,
      cost: 0,
      params: {},
      finalPassed: 3,
    }) satisfies Trajectory,
);

const snapshotTrajectories: Trajectory[] = [
  {
    id: "traj-1",
    model: "codex/gpt-5.3-codex",
    environment: "c_compiler",
    commits: [],
    totalTests: 10,
    startedAt: "2026-03-08T00:00:00.000Z",
    duration: "5m",
    totalTokens: 100,
    cost: 0,
    params: {},
    finalPassed: 3,
  },
  {
    id: "traj-2",
    model: "codex/gpt-5.3-codex",
    environment: "c_compiler",
    commits: [],
    totalTests: 10,
    startedAt: "2026-03-08T00:05:00.000Z",
    duration: "6m",
    totalTokens: 200,
    cost: 0,
    params: {},
    finalPassed: 4,
  },
];

const getProjectSnapshotMock = vi.fn(async () => ({
  project: {
    name: "c-compiler",
    description: "Compiler project",
    createdAt: "2026-03-01T00:00:00.000Z",
    updatedAt: "2026-03-08T00:00:00.000Z",
    trajectoryCount: 2,
    environmentCount: 1,
    modelCount: 1,
  },
  manifest: {
    project: "c-compiler",
    revision: "rev-1",
    publishedAt: "2026-03-08T00:10:00.000Z",
    trajectoryCount: 2,
    liveTrajectoryCount: 1,
    agents: [],
    objects: {
      trajectoriesIndex: { key: "trajectories.index.json", sizeBytes: 1 },
      compareIndex: { key: "compare.index.json", sizeBytes: 1 },
      setupsIndex: { key: "setups.index.json", sizeBytes: 1 },
      liveIndex: { key: "live.index.json", sizeBytes: 1 },
      trajectories: {},
    },
  },
  trajectories: snapshotTrajectories,
  compare: snapshotTrajectories,
  setups: snapshotTrajectories,
  live: {
    revision: "rev-1",
    updatedAt: "2026-03-08T00:10:00.000Z",
    trajectoryIds: ["traj-2"],
    liveTrajectoryCount: 1,
  },
  details: new Map<string, Trajectory>(),
}));

vi.mock("@/lib/server/project-context", () => ({
  getProjectFromRequest: getProjectFromRequestMock,
}));

vi.mock("@/lib/server/project-snapshot-store", () => ({
  getProjectsForUi: getProjectsForUiMock,
  getProjectSnapshot: getProjectSnapshotMock,
  getTrajectoryDetailFromSnapshot: getTrajectoryDetailFromSnapshotMock,
  getTrajectoryLogsFromSnapshot: vi.fn(async () => []),
  getCodeHistoryChunkFromSnapshot: vi.fn(async () => ({})),
}));

describe("serving-plane routes", () => {
  beforeEach(() => {
    getProjectFromRequestMock.mockClear();
    getProjectsForUiMock.mockClear();
    getProjectSnapshotMock.mockClear();
    getTrajectoryDetailFromSnapshotMock.mockClear();
    vi.resetModules();
  });

  it("lists only serving-plane UI projects", async () => {
    const mod = await import("../projects/route");
    const response = await mod.GET();
    const body = await response.json();

    expect(getProjectsForUiMock).toHaveBeenCalled();
    expect(body.projects).toHaveLength(1);
    expect(body.projects[0]?.name).toBe("c-compiler");
  });

  it("serves revision metadata from the project snapshot", async () => {
    const mod = await import("../revision/route");
    const request = new NextRequest(
      "http://localhost/api/revision?project=c-compiler",
    );
    const response = await mod.GET(request);
    const body = await response.json();

    expect(getProjectSnapshotMock).toHaveBeenCalledWith("c-compiler");
    expect(body.dataVersion).toBe("rev-1");
    expect(body.loadedRevision).toBe("rev-1");
  });

  it("serves the trajectory list from the project snapshot", async () => {
    const mod = await import("../trajectories/route");
    const request = new NextRequest(
      "http://localhost/api/trajectories?project=c-compiler",
    );
    const response = await mod.GET(request);
    const body = await response.json();

    expect(getProjectSnapshotMock).toHaveBeenCalledWith("c-compiler");
    expect(body).toHaveLength(2);
    expect(body[0]?.id).toBe("traj-1");
  });

  it("resolves compare selections through snapshot-backed detail lookups", async () => {
    const mod = await import("../compare/route");
    const request = new NextRequest(
      "http://localhost/api/compare?project=c-compiler&ids=traj-1,traj-2",
    );
    const response = await mod.GET(request);
    const body = await response.json();

    expect(getProjectSnapshotMock).toHaveBeenCalledWith("c-compiler");
    expect(getTrajectoryDetailFromSnapshotMock).toHaveBeenNthCalledWith(
      1,
      "c-compiler",
      "traj-1",
    );
    expect(getTrajectoryDetailFromSnapshotMock).toHaveBeenNthCalledWith(
      2,
      "c-compiler",
      "traj-2",
    );
    expect(body).toHaveLength(2);
  });

  it("serves trajectory detail from the snapshot store", async () => {
    const mod = await import("../trajectories/[id]/route");
    const request = new NextRequest(
      "http://localhost/api/trajectories/traj-1?project=c-compiler",
    );
    const response = await mod.GET(request, {
      params: Promise.resolve({ id: "traj-1" }),
    });
    const body = await response.json();

    expect(getTrajectoryDetailFromSnapshotMock).toHaveBeenCalledWith(
      "c-compiler",
      "traj-1",
    );
    expect(body.id).toBe("traj-1");
  });
});
