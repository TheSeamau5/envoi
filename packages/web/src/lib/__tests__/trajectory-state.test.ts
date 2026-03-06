import { describe, expect, it } from "vitest";
import { isTrajectoryActive } from "../trajectory-state";
import type { Trajectory } from "../types";

function makeTrajectory(
  overrides: Partial<Trajectory> = {},
): Trajectory {
  return {
    id: "traj-001",
    model: "codex/gpt-5",
    environment: "c_compiler",
    commits: [],
    totalTests: 0,
    startedAt: "2026-01-01T00:00:00Z",
    duration: "0 min",
    totalTokens: 0,
    cost: 0,
    params: {},
    finalPassed: 0,
    ...overrides,
  };
}

describe("isTrajectoryActive", () => {
  it("treats live trajectories as active", () => {
    expect(isTrajectoryActive(makeTrajectory(), { live: true })).toBe(true);
  });

  it("treats scored trajectories as active", () => {
    expect(
      isTrajectoryActive(makeTrajectory({ finalPassed: 12 })),
    ).toBe(true);
  });

  it("treats substantial work by total parts as active", () => {
    expect(
      isTrajectoryActive(makeTrajectory({ totalParts: 3 })),
    ).toBe(true);
  });

  it("keeps zero-score short runs in failed", () => {
    expect(
      isTrajectoryActive(makeTrajectory({ totalParts: 2 })),
    ).toBe(false);
  });
});
