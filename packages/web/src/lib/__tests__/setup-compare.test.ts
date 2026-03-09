import { describe, expect, it } from "vitest";

import {
  buildDifficultyCells,
  computeBestCurve,
  getTraceBestMinutes,
  getTraceBestPassed,
  getTraceBestPercent,
  getTraceBestSuitePassed,
  pickBestTrace,
} from "../setup-compare";
import type { Commit, Trajectory } from "../types";

function makeCommit(
  index: number,
  minutesElapsed: number,
  totalPassed: number,
  suiteState: Record<string, number> = {},
): Commit {
  return {
    index,
    hash: `commit-${index}`,
    turn: index,
    timestamp: `2026-01-01T00:${String(index).padStart(2, "0")}:00Z`,
    minutesElapsed,
    suiteState,
    totalPassed,
    delta: 0,
    isRegression: false,
    isMilestone: false,
    feedback: {
      passedDelta: 0,
      newlyBroken: 0,
      newlyFixed: 0,
      brokenTests: [],
      totalPassed,
      totalFailed: 0,
    },
    steps: [],
    changedFiles: [],
    codeSnapshot: {},
    phase: 0,
    tokensUsed: 0,
  };
}

function makeTrajectory(
  id: string,
  commits: Commit[],
  overrides: Partial<Trajectory> = {},
): Trajectory {
  const lastCommit = commits[commits.length - 1];
  return {
    id,
    model: "codex/gpt-5.3-codex",
    environment: "c_compiler",
    commits,
    totalTests: 100,
    startedAt: "2026-01-01T00:00:00Z",
    duration: "1 hr",
    totalTokens: 0,
    cost: 0,
    params: {},
    finalPassed: lastCommit?.totalPassed ?? 0,
    ...overrides,
  };
}

describe("setup compare helpers", () => {
  it("picks the strongest trace by peak passed count first", () => {
    const higherPassedButLowerPercent = makeTrajectory(
      "trace-b",
      [makeCommit(0, 10, 15), makeCommit(1, 30, 40)],
      { totalTests: 100 },
    );
    const higherPercent = makeTrajectory(
      "trace-a",
      [makeCommit(0, 8, 20), makeCommit(1, 20, 30)],
      { totalTests: 50 },
    );

    const bestTrace = pickBestTrace([
      higherPassedButLowerPercent,
      higherPercent,
    ]);

    expect(bestTrace?.id).toBe("trace-b");
    expect(getTraceBestPassed(higherPassedButLowerPercent)).toBe(40);
    expect(getTraceBestPercent(higherPassedButLowerPercent)).toBe(40);
    expect(getTraceBestMinutes(higherPassedButLowerPercent)).toBe(30);
  });

  it("builds the selected trace's actual curve up to the best commit", () => {
    const regressedTrace = makeTrajectory(
      "trace-regressed",
      [
        makeCommit(0, 10, 10, { basics: 4 }),
        makeCommit(1, 20, 45, { basics: 20 }),
        makeCommit(2, 40, 12, { basics: 6 }),
      ],
      { finalPassed: 45 },
    );

    const curve = computeBestCurve(regressedTrace, 40);
    const lastPoint = curve[curve.length - 1];

    expect(lastPoint?.minutes).toBe(20);
    expect(lastPoint?.passedPct).toBe(45);
  });

  it("uses suite scores from the best commit only", () => {
    const trace = makeTrajectory(
      "trace-suite-snapshot",
      [
        makeCommit(0, 5, 10, { basics: 10, wacct: 0 }),
        makeCommit(1, 15, 10, { basics: 0, wacct: 10 }),
      ],
      { finalPassed: 10 },
    );

    expect(getTraceBestSuitePassed(trace, "basics")).toBe(10);
    expect(getTraceBestSuitePassed(trace, "wacct")).toBe(0);
    expect(getTraceBestSuitePassed(trace, "torture")).toBe(0);
  });

  it("derives difficulty cells from the same best-trace table used by setups", () => {
    const suites = [
      { name: "basics", total: 35 },
      { name: "c_testsuite", total: 220 },
      { name: "torture", total: 1481 },
      { name: "wacct", total: 1559 },
    ];
    const traces = [
      makeTrajectory(
        "trace-gpt-53",
        [
          makeCommit(0, 42, 980, {
            basics: 35,
            c_testsuite: 71,
            torture: 6,
            wacct: 868,
          }),
        ],
        {
          model: "codex/gpt-5.3-codex",
          totalTests: 3295,
          finalPassed: 980,
          suites,
        },
      ),
      makeTrajectory(
        "trace-gpt-54",
        [
          makeCommit(0, 31, 1211, {
            basics: 35,
            c_testsuite: 89,
            torture: 55,
            wacct: 1032,
          }),
        ],
        {
          model: "codex/gpt-5.4",
          totalTests: 3295,
          finalPassed: 1211,
          suites,
        },
      ),
      makeTrajectory(
        "trace-sonnet",
        [
          makeCommit(0, 28, 926, {
            basics: 25,
            c_testsuite: 92,
            torture: 102,
            wacct: 707,
          }),
        ],
        {
          model: "claude_code/claude-sonnet-4-6",
          totalTests: 3285,
          finalPassed: 926,
          suites,
        },
      ),
    ];

    expect(buildDifficultyCells(traces)).toEqual([
      {
        attempts: 1,
        category: "basics",
        environment: "c_compiler",
        model: "claude_code/claude-sonnet-4-6",
        passRate: 25 / 35,
      },
      {
        attempts: 1,
        category: "basics",
        environment: "c_compiler",
        model: "codex/gpt-5.3-codex",
        passRate: 1,
      },
      {
        attempts: 1,
        category: "basics",
        environment: "c_compiler",
        model: "codex/gpt-5.4",
        passRate: 1,
      },
      {
        attempts: 1,
        category: "c_testsuite",
        environment: "c_compiler",
        model: "claude_code/claude-sonnet-4-6",
        passRate: 92 / 220,
      },
      {
        attempts: 1,
        category: "c_testsuite",
        environment: "c_compiler",
        model: "codex/gpt-5.3-codex",
        passRate: 71 / 220,
      },
      {
        attempts: 1,
        category: "c_testsuite",
        environment: "c_compiler",
        model: "codex/gpt-5.4",
        passRate: 89 / 220,
      },
      {
        attempts: 1,
        category: "torture",
        environment: "c_compiler",
        model: "claude_code/claude-sonnet-4-6",
        passRate: 102 / 1481,
      },
      {
        attempts: 1,
        category: "torture",
        environment: "c_compiler",
        model: "codex/gpt-5.3-codex",
        passRate: 6 / 1481,
      },
      {
        attempts: 1,
        category: "torture",
        environment: "c_compiler",
        model: "codex/gpt-5.4",
        passRate: 55 / 1481,
      },
      {
        attempts: 1,
        category: "wacct",
        environment: "c_compiler",
        model: "claude_code/claude-sonnet-4-6",
        passRate: 707 / 1559,
      },
      {
        attempts: 1,
        category: "wacct",
        environment: "c_compiler",
        model: "codex/gpt-5.3-codex",
        passRate: 868 / 1559,
      },
      {
        attempts: 1,
        category: "wacct",
        environment: "c_compiler",
        model: "codex/gpt-5.4",
        passRate: 1032 / 1559,
      },
    ]);
  });
});
