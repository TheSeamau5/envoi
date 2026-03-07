import { beforeEach, describe, expect, it, vi } from "vitest";

const cachedMock = vi.fn(async (_key: string, fn: () => Promise<unknown>) =>
  fn(),
);
const getActiveProjectMock = vi.fn(async (project?: string) => project ?? "default");
const ensureProjectDataFreshnessMock = vi.fn(async () => ({
  hasManifest: false,
  inSync: false,
  revisionLagMs: 0,
  dataVersion: "version-1",
  rawSyncInFlight: false,
  summarySyncInFlight: false,
}));
const getProjectDataStatusMock = vi.fn(async () => ({
  hasManifest: false,
  inSync: false,
  revisionLagMs: 0,
  dataVersion: "version-1",
  rawSyncInFlight: false,
  summarySyncInFlight: false,
}));

vi.mock("../cache", () => ({
  cached: cachedMock,
}));

vi.mock("../db", () => ({
  getActiveProject: getActiveProjectMock,
  ensureProjectDataFreshness: ensureProjectDataFreshnessMock,
  getProjectDataStatus: getProjectDataStatusMock,
  buildProjectDataHeaders: (status: { dataVersion?: string }) => ({
    "x-envoi-data-version": status.dataVersion ?? "",
  }),
}));

describe("server project data coordinator", () => {
  beforeEach(() => {
    cachedMock.mockClear();
    getActiveProjectMock.mockClear();
    ensureProjectDataFreshnessMock.mockClear();
    getProjectDataStatusMock.mockClear();
    vi.resetModules();
  });

  it("maps fresh boolean to centralized freshness mode", async () => {
    const mod = await import("../project-data");

    expect(mod.freshnessFromBool(true)).toBe("force");
    expect(mod.freshnessFromBool(false)).toBe("cached");
    expect(mod.freshnessFromBool(undefined)).toBe("cached");
  });

  it("uses cache only for cached reads", async () => {
    const mod = await import("../project-data");
    const loadMock = vi.fn(async (project: string) => `${project}:loaded`);

    const result = await mod.readProjectData({
      project: "c-compiler",
      freshness: "cached",
      cacheKey: "cache-key",
      load: loadMock,
    });

    expect(result).toBe("c-compiler:loaded");
    expect(cachedMock).toHaveBeenCalledWith(
      "cache-key",
      expect.any(Function),
      undefined,
    );
    expect(ensureProjectDataFreshnessMock).toHaveBeenCalledWith(
      "c-compiler",
      { mode: "cached" },
    );
  });

  it("bypasses cache for fresh and force reads", async () => {
    const mod = await import("../project-data");
    const loadMock = vi.fn(async (project: string) => `${project}:fresh`);

    const freshResult = await mod.readProjectData({
      project: "c-compiler",
      freshness: "fresh",
      cacheKey: "cache-key",
      load: loadMock,
    });
    const forceResult = await mod.readProjectData({
      project: "c-compiler",
      freshness: "force",
      cacheKey: "cache-key",
      load: loadMock,
    });

    expect(freshResult).toBe("c-compiler:fresh");
    expect(forceResult).toBe("c-compiler:fresh");
    expect(cachedMock).not.toHaveBeenCalled();
    expect(ensureProjectDataFreshnessMock).toHaveBeenNthCalledWith(
      1,
      "c-compiler",
      { mode: "fresh" },
    );
    expect(ensureProjectDataFreshnessMock).toHaveBeenNthCalledWith(
      2,
      "c-compiler",
      { mode: "force" },
    );
  });

  it("delegates status reads through the centralized db status path", async () => {
    const mod = await import("../project-data");

    const status = await mod.readProjectDataStatus("c-compiler", {
      forceCheck: true,
      mode: "fresh",
    });

    expect(status.dataVersion).toBe("version-1");
    expect(getProjectDataStatusMock).toHaveBeenCalledWith(
      "c-compiler",
      {
        forceCheck: true,
        mode: "fresh",
      },
    );
  });
});
