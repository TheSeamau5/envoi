/**
 * Tests for trajectory reconstruction logic.
 * Focused on evaluation data correctness — the core bug being fixed.
 */

import { describe, it, expect } from "vitest";
import {
  buildEvaluationsFromRows,
  reconstructTrajectory,
  type ParquetRow,
} from "../reconstruct";

// ---------------------------------------------------------------------------
// Helpers to build minimal ParquetRow fixtures
// ---------------------------------------------------------------------------

function makeRow(overrides: Partial<ParquetRow> & { part: number }): ParquetRow {
  return {
    trajectory_id: "test-trajectory",
    agent: "test-agent",
    agent_model: "test-model",
    started_at: "2025-01-01T00:00:00Z",
    environment: "test-env",
    part: overrides.part,
    timestamp: overrides.timestamp ?? new Date(Date.now() + overrides.part * 1000).toISOString(),
    role: "assistant",
    part_type: overrides.part_type ?? "tool_call",
    summary: overrides.summary ?? `Step at part ${overrides.part}`,
    git_commit: overrides.git_commit ?? "abc123",
    turn: overrides.turn ?? 1,
    ...overrides,
  };
}

function makeEvalEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    kind: "commit_async",
    eval_id: "eval-001",
    target_commit: "commit-aaa",
    trigger_part: 0,
    status: "queued",
    passed: 0,
    failed: 0,
    total: 0,
    suite_results: {},
    ...overrides,
  };
}

function rowWithEval(
  part: number,
  events: Record<string, unknown>[],
  extra: Partial<ParquetRow> = {},
): ParquetRow {
  return makeRow({
    part,
    eval_events_delta: JSON.stringify(events),
    ...extra,
  });
}

// ---------------------------------------------------------------------------
// buildEvaluationsFromRows tests
// ---------------------------------------------------------------------------

describe("buildEvaluationsFromRows", () => {
  it("parses a single completed eval event", () => {
    const rows = [
      rowWithEval(10, [
        makeEvalEvent({
          target_commit: "commit-aaa",
          status: "completed",
          passed: 909,
          failed: 1275,
          total: 2184,
          suite_results: {
            basics: { passed: 35, total: 35 },
            c_testsuite: { passed: 200, total: 220 },
          },
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    expect(result.size).toBe(1);

    const rec = result.get("commit-aaa");
    expect(rec).toBeDefined();
    expect(rec?.status).toBe("completed");
    expect(rec?.passed).toBe(909);
    expect(rec?.failed).toBe(1275);
    expect(rec?.total).toBe(2184);
    expect(rec?.suiteResults.basics).toEqual({ passed: 35, total: 35 });
  });

  it("does NOT let a queued event overwrite a completed event for the same commit", () => {
    const rows = [
      // Part 10: completed eval with real results
      rowWithEval(10, [
        makeEvalEvent({
          target_commit: "commit-aaa",
          status: "completed",
          passed: 909,
          failed: 1275,
          total: 2184,
          suite_results: { basics: { passed: 35, total: 35 } },
        }),
      ]),
      // Part 50: a re-trigger queued event for the same commit
      rowWithEval(50, [
        makeEvalEvent({
          target_commit: "commit-aaa",
          status: "queued",
          passed: 0,
          failed: 0,
          total: 0,
          suite_results: {},
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    const rec = result.get("commit-aaa");
    expect(rec).toBeDefined();
    expect(rec?.status).toBe("completed");
    expect(rec?.passed).toBe(909);
    expect(rec?.total).toBe(2184);
    expect(rec?.suiteResults.basics).toEqual({ passed: 35, total: 35 });
  });

  it("does NOT let a running event overwrite a completed event", () => {
    const rows = [
      rowWithEval(10, [
        makeEvalEvent({
          target_commit: "commit-aaa",
          status: "completed",
          passed: 500,
          failed: 100,
          total: 600,
        }),
      ]),
      rowWithEval(20, [
        makeEvalEvent({
          target_commit: "commit-aaa",
          status: "running",
          passed: 0,
          failed: 0,
          total: 0,
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    const rec = result.get("commit-aaa");
    expect(rec?.status).toBe("completed");
    expect(rec?.passed).toBe(500);
    expect(rec?.total).toBe(600);
  });

  it("allows normal progression: queued → completed", () => {
    const rows = [
      rowWithEval(5, [
        makeEvalEvent({
          target_commit: "commit-bbb",
          status: "queued",
          passed: 0,
          failed: 0,
          total: 0,
        }),
      ]),
      rowWithEval(30, [
        makeEvalEvent({
          target_commit: "commit-bbb",
          status: "completed",
          passed: 400,
          failed: 200,
          total: 600,
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    const rec = result.get("commit-bbb");
    expect(rec?.status).toBe("completed");
    expect(rec?.passed).toBe(400);
    expect(rec?.total).toBe(600);
  });

  it("allows a later completed event to update an earlier completed event (re-eval)", () => {
    const rows = [
      rowWithEval(10, [
        makeEvalEvent({
          target_commit: "commit-ccc",
          status: "completed",
          passed: 100,
          failed: 500,
          total: 600,
        }),
      ]),
      rowWithEval(50, [
        makeEvalEvent({
          target_commit: "commit-ccc",
          status: "completed",
          passed: 300,
          failed: 300,
          total: 600,
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    const rec = result.get("commit-ccc");
    expect(rec?.status).toBe("completed");
    expect(rec?.passed).toBe(300);
    expect(rec?.total).toBe(600);
  });

  it("handles multiple different commits independently", () => {
    const rows = [
      rowWithEval(10, [
        makeEvalEvent({
          target_commit: "commit-aaa",
          status: "completed",
          passed: 100,
          total: 200,
        }),
        makeEvalEvent({
          target_commit: "commit-bbb",
          status: "completed",
          passed: 300,
          total: 400,
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    expect(result.size).toBe(2);
    expect(result.get("commit-aaa")?.passed).toBe(100);
    expect(result.get("commit-bbb")?.passed).toBe(300);
  });

  it("processes turn_end_blocking events (not just commit_async)", () => {
    const rows = [
      rowWithEval(10, [
        makeEvalEvent({
          kind: "commit_async",
          target_commit: "commit-aaa",
          status: "queued",
          passed: 0,
          total: 0,
        }),
      ]),
      rowWithEval(20, [
        {
          kind: "turn_end_blocking",
          eval_id: "eval-blocking",
          target_commit: "commit-aaa",
          trigger_part: 20,
          status: "completed",
          passed: 909,
          failed: 1275,
          total: 2184,
          suite_results: {},
        },
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    expect(result.size).toBe(1);
    expect(result.get("commit-aaa")?.status).toBe("completed");
    expect(result.get("commit-aaa")?.passed).toBe(909);
    expect(result.get("commit-aaa")?.total).toBe(2184);
  });

  it("ignores events without target_commit", () => {
    const rows = [
      rowWithEval(10, [
        { kind: "something_else", status: "completed", passed: 999 },
        makeEvalEvent({
          target_commit: "commit-aaa",
          status: "completed",
          passed: 50,
          total: 100,
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    expect(result.size).toBe(1);
    expect(result.get("commit-aaa")?.passed).toBe(50);
  });

  it("handles events within the same row in order (queued then completed)", () => {
    const rows = [
      rowWithEval(10, [
        makeEvalEvent({
          target_commit: "commit-ddd",
          status: "queued",
          passed: 0,
          total: 0,
        }),
        makeEvalEvent({
          target_commit: "commit-ddd",
          status: "completed",
          passed: 750,
          total: 1000,
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    const rec = result.get("commit-ddd");
    expect(rec?.status).toBe("completed");
    expect(rec?.passed).toBe(750);
  });

  it("handles events within the same row (completed then queued) — completed wins", () => {
    const rows = [
      rowWithEval(10, [
        makeEvalEvent({
          target_commit: "commit-eee",
          status: "completed",
          passed: 750,
          total: 1000,
        }),
        makeEvalEvent({
          target_commit: "commit-eee",
          status: "queued",
          passed: 0,
          total: 0,
        }),
      ]),
    ];

    const result = buildEvaluationsFromRows(rows);
    const rec = result.get("commit-eee");
    expect(rec?.status).toBe("completed");
    expect(rec?.passed).toBe(750);
    expect(rec?.total).toBe(1000);
  });
});

// ---------------------------------------------------------------------------
// reconstructTrajectory end-to-end tests
// ---------------------------------------------------------------------------

describe("reconstructTrajectory", () => {
  it("produces correct totalPassed for a simple trajectory with completed evals", () => {
    const rows: ParquetRow[] = [
      makeRow({
        part: 0,
        git_commit: "commit-001",
        summary: "initial setup",
        eval_events_delta: JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-001",
            status: "completed",
            passed: 10,
            failed: 90,
            total: 100,
            suite_results: { basics: { passed: 10, total: 100 } },
          }),
        ]),
      }),
      makeRow({
        part: 1,
        git_commit: "commit-002",
        summary: "fix bug",
        eval_events_delta: JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-002",
            status: "completed",
            passed: 50,
            failed: 50,
            total: 100,
            suite_results: { basics: { passed: 50, total: 100 } },
          }),
        ]),
      }),
    ];

    const trajectory = reconstructTrajectory(rows);
    expect(trajectory.commits.length).toBe(2);
    expect(trajectory.commits[0]?.totalPassed).toBe(10);
    expect(trajectory.commits[1]?.totalPassed).toBe(50);
    expect(trajectory.finalPassed).toBe(50);
  });

  it("queued event after completed does NOT corrupt the trajectory", () => {
    const rows: ParquetRow[] = [
      // Part 0: the agent writes code
      makeRow({
        part: 0,
        git_commit: "commit-001",
        summary: "write solution",
      }),
      // Part 5: eval completes with 909/2184
      makeRow({
        part: 5,
        git_commit: "commit-001",
        summary: "eval result arrives",
        eval_events_delta: JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-001",
            status: "completed",
            passed: 909,
            failed: 1275,
            total: 2184,
            suite_results: {
              basics: { passed: 35, total: 35 },
              c_testsuite: { passed: 200, total: 220 },
              torture: { passed: 370, total: 370 },
              wacct: { passed: 304, total: 1559 },
            },
          }),
        ]),
      }),
      // Part 20: a re-trigger event (queued) — should NOT overwrite
      makeRow({
        part: 20,
        git_commit: "commit-001",
        summary: "another step",
        eval_events_delta: JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-001",
            status: "queued",
            passed: 0,
            failed: 0,
            total: 0,
          }),
        ]),
      }),
    ];

    const trajectory = reconstructTrajectory(rows);
    // Should have 1 commit (commit-001), not 0
    expect(trajectory.commits.length).toBeGreaterThanOrEqual(1);

    // Find the commit for commit-001
    const commit = trajectory.commits.find((c) => c.hash === "commit-001");
    expect(commit).toBeDefined();
    expect(commit?.totalPassed).toBe(909);
    expect(commit?.feedback.totalPassed).toBe(909);
    expect(trajectory.finalPassed).toBe(909);
  });

  it("produces single fallback commit when no eval events exist", () => {
    const rows: ParquetRow[] = [
      makeRow({ part: 0, summary: "step 1" }),
      makeRow({ part: 1, summary: "step 2" }),
      makeRow({ part: 2, summary: "step 3" }),
    ];

    const trajectory = reconstructTrajectory(rows);
    // No evals → single fallback commit
    expect(trajectory.commits.length).toBe(1);
    expect(trajectory.commits[0]?.totalPassed).toBe(0);
  });

  it("carries forward prevTotalPassed for non-completed evals", () => {
    const rows: ParquetRow[] = [
      makeRow({
        part: 0,
        git_commit: "commit-001",
        summary: "first commit",
        eval_events_delta: JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-001",
            status: "completed",
            passed: 100,
            failed: 100,
            total: 200,
          }),
        ]),
      }),
      makeRow({
        part: 10,
        git_commit: "commit-002",
        summary: "second commit",
        eval_events_delta: JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-002",
            status: "queued",
            passed: 0,
            failed: 0,
            total: 0,
          }),
        ]),
      }),
    ];

    const trajectory = reconstructTrajectory(rows);
    expect(trajectory.commits.length).toBe(2);
    // First commit: completed, shows 100
    expect(trajectory.commits[0]?.totalPassed).toBe(100);
    // Second commit: queued, carries forward 100
    expect(trajectory.commits[1]?.totalPassed).toBe(100);
  });

  it("handles the realistic c-compiler scenario: multiple evals across many parts", () => {
    // Simulate: 3 commits, each with a completed eval.
    // Third commit gets a queued re-trigger after completion.
    const rows: ParquetRow[] = [];

    // Commit 1: parts 0-5
    for (let part = 0; part <= 5; part++) {
      rows.push(
        makeRow({
          part,
          git_commit: "commit-001",
          summary: `work on part ${part}`,
          eval_events_delta:
            part === 5
              ? JSON.stringify([
                  makeEvalEvent({
                    target_commit: "commit-001",
                    status: "completed",
                    passed: 13,
                    failed: 22,
                    total: 35,
                    suite_results: { basics: { passed: 13, total: 35 } },
                  }),
                ])
              : undefined,
        }),
      );
    }

    // Commit 2: parts 6-15
    for (let part = 6; part <= 15; part++) {
      rows.push(
        makeRow({
          part,
          git_commit: "commit-002",
          summary: `work on part ${part}`,
          eval_events_delta:
            part === 15
              ? JSON.stringify([
                  makeEvalEvent({
                    target_commit: "commit-002",
                    status: "completed",
                    passed: 200,
                    failed: 1984,
                    total: 2184,
                    suite_results: {
                      basics: { passed: 35, total: 35 },
                      c_testsuite: { passed: 100, total: 220 },
                      torture: { passed: 65, total: 370 },
                      wacct: { passed: 0, total: 1559 },
                    },
                  }),
                ])
              : undefined,
        }),
      );
    }

    // Commit 3: parts 16-30, completed at part 25, queued re-trigger at part 30
    for (let part = 16; part <= 30; part++) {
      let evalDelta: string | undefined;
      if (part === 25) {
        evalDelta = JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-003",
            status: "completed",
            passed: 909,
            failed: 1275,
            total: 2184,
            suite_results: {
              basics: { passed: 35, total: 35 },
              c_testsuite: { passed: 200, total: 220 },
              torture: { passed: 370, total: 370 },
              wacct: { passed: 304, total: 1559 },
            },
          }),
        ]);
      } else if (part === 30) {
        // Queued re-trigger — must NOT overwrite 909
        evalDelta = JSON.stringify([
          makeEvalEvent({
            target_commit: "commit-003",
            status: "queued",
            passed: 0,
            failed: 0,
            total: 0,
          }),
        ]);
      }
      rows.push(
        makeRow({
          part,
          git_commit: "commit-003",
          summary: `work on part ${part}`,
          eval_events_delta: evalDelta,
        }),
      );
    }

    const trajectory = reconstructTrajectory(rows);

    // Should have 3 commits (plus possibly trailing rows)
    expect(trajectory.commits.length).toBeGreaterThanOrEqual(3);

    const commitOne = trajectory.commits[0];
    const commitTwo = trajectory.commits[1];
    const commitThree = trajectory.commits[2];

    expect(commitOne?.totalPassed).toBe(13);
    expect(commitTwo?.totalPassed).toBe(200);
    expect(commitThree?.totalPassed).toBe(909);

    // Final passed should be 909
    expect(trajectory.finalPassed).toBe(909);

    // Suite state on commit 3 should have all suites (SuiteState maps name → passed count)
    expect(commitThree?.suiteState.basics).toBe(35);
    expect(commitThree?.suiteState.c_testsuite).toBe(200);
    expect(commitThree?.suiteState.torture).toBe(370);
    expect(commitThree?.suiteState.wacct).toBe(304);
  });
});
