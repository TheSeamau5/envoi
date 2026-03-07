import { beforeEach, describe, expect, it, vi } from "vitest";

const queryMock = vi.fn(async () => []);
const readProjectDataMock = vi.fn(
  async (options: { project?: string; load: (project: string) => Promise<unknown> }) =>
    options.load(options.project ?? "c-compiler"),
);

vi.mock("../project-data", () => ({
  readProjectData: readProjectDataMock,
  freshnessFromBool: (fresh: boolean | undefined) =>
    fresh === true ? "force" : "cached",
}));

vi.mock("../db", () => ({
  isS3Configured: () => true,
  traceUri: async () => "s3://bucket/project/c-compiler/trajectories/traj-1/trace.parquet",
  freshTraceUri: async () => "s3://bucket/project/c-compiler/trajectories/traj-1/trace.parquet",
  codeSnapshotsUri: async () => undefined,
  logsUri: async () => "s3://bucket/project/c-compiler/trajectories/traj-1/logs.parquet",
  freshLogsUri: async () => "s3://bucket/project/c-compiler/trajectories/traj-1/logs.parquet",
  query: queryMock,
}));

describe("server data freshness readers", () => {
  beforeEach(() => {
    queryMock.mockReset();
    readProjectDataMock.mockClear();
    vi.resetModules();
  });

  it("builds list rows from the unified trajectories table", async () => {
    queryMock.mockResolvedValue([
      {
        trajectory_id: "traj-live",
        environment: "c_compiler",
        agent_model: "gpt-5.3-codex",
        agent: "codex",
        started_at: "2026-03-06T20:45:14.000Z",
        ended_at: "2026-03-06T20:57:00.000Z",
        total_parts: 52,
        total_turns: 2,
        total_tokens: 1234,
        session_end_reason: undefined,
        task_params: "{}",
        suites: JSON.stringify({
          "all/basics/smoke": { passed: 3, total: 7 },
          "all/wacct/chapter_1": { passed: 5, total: 10 },
        }),
        sandbox_id: "sb-123",
        sandbox_provider: "modal",
        best_passed: null,
        best_failed: null,
        best_total: null,
        eval_count: 0,
      },
    ]);

    const { getAllTrajectories } = await import("../data");
    const trajectories = await getAllTrajectories({
      project: "c-compiler",
      fresh: true,
    });

    expect(trajectories).toHaveLength(1);
    expect(trajectories[0]?.id).toBe("traj-live");
    expect(trajectories[0]?.finalPassed).toBe(8);
    expect(trajectories[0]?.sandboxId).toBe("sb-123");
    expect(trajectories[0]?.sandboxProvider).toBe("modal");
  });

  it("uses materialized table data directly for active trajectories without per-file reads", async () => {
    queryMock.mockResolvedValue([
      {
        trajectory_id: "traj-live",
        environment: "c_compiler",
        agent_model: "gpt-5.3-codex",
        agent: "codex",
        started_at: "2026-03-06T20:45:14.000Z",
        ended_at: "2026-03-06T21:16:50.852Z",
        total_parts: 381,
        total_turns: 2,
        total_tokens: 1234,
        session_end_reason: undefined,
        task_params: "{}",
        suites: "{}",
        sandbox_id: "sb-123",
        sandbox_provider: "modal",
        best_passed: 921,
        best_failed: 2364,
        best_total: 3285,
        eval_count: 1,
      },
    ]);

    const { getAllTrajectories } = await import("../data");
    const trajectories = await getAllTrajectories({
      project: "c-compiler",
      fresh: true,
    });

    expect(trajectories[0]?.duration).toBe("32m");
    expect(trajectories[0]?.finalPassed).toBe(921);
    expect(trajectories[0]?.totalParts).toBe(381);
    // Only one query to the trajectories table — no per-trajectory parquet reads
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it("returns sandbox metadata from the materialized trajectories table", async () => {
    queryMock.mockResolvedValue([
      {
        session_end_reason: undefined,
        sandbox_id: "sb-live",
        sandbox_provider: "modal",
      },
    ]);

    const { getTrajectorySandboxMeta } = await import("../data");
    const meta = await getTrajectorySandboxMeta("traj-live", "c-compiler");

    expect(meta).toEqual({
      sessionEndReason: undefined,
      sandboxId: "sb-live",
      sandboxProvider: "modal",
    });
  });

  it("loads compare trajectories through trajectory detail reconstruction", async () => {
    queryMock.mockImplementation(async (sql: string) => {
      if (sql.includes("FROM evaluations")) {
        return [];
      }
      if (sql.includes("FROM read_parquet")) {
        return [
          {
            trajectory_id: "traj-1",
            session_id: "sess-1",
            agent: "codex",
            agent_model: "gpt-5.3-codex",
            started_at: "2026-03-06T20:45:14.000Z",
            environment: "c_compiler",
            task_params: "{}",
            part: 0,
            timestamp: "2026-03-06T20:45:20.000Z",
            role: "assistant",
            part_type: "text",
            summary: "hello",
            git_commit: "abc123",
            turn: 1,
            suites: JSON.stringify({
              "all/basics/smoke": { passed: 3, total: 7 },
            }),
          },
        ];
      }
      return [];
    });

    const { getCompareTrajectories } = await import("../data");
    const trajectories = await getCompareTrajectories({
      project: "c-compiler",
      ids: ["traj-1"],
      fresh: true,
    });

    expect(trajectories).toHaveLength(1);
    expect(trajectories[0]?.id).toBe("traj-1");
    expect(trajectories[0]?.commits).toHaveLength(1);
  });

  it("derives difficulty cells from the unified trajectories table", async () => {
    queryMock.mockResolvedValue([
      {
        category: "basics",
        model: "codex/gpt-5.3-codex",
        pass_rate: 0.5,
        attempts: 4,
      },
    ]);

    const { getDifficultyData } = await import("../data");
    const cells = await getDifficultyData("c-compiler", { fresh: true });

    expect(cells).toEqual([
      {
        environment: "c_compiler",
        category: "basics",
        model: "codex/gpt-5.3-codex",
        passRate: 0.5,
        attempts: 4,
      },
    ]);
  });

  it("derives portfolio environment rows from unified trajectory data", async () => {
    queryMock.mockResolvedValue([
      {
        environment: "c_compiler",
        max_passed: 10,
        max_total: 20,
        best_model: "codex/gpt-5.3-codex",
        median_pass_rate: 0.5,
        run_count: 3,
        total_tokens: 12345,
      },
    ]);

    const { getPortfolioEnvironmentData } = await import("../data");
    const rows = await getPortfolioEnvironmentData("c-compiler", {
      fresh: true,
    });

    expect(rows).toEqual([
      {
        environment: "c_compiler",
        bestPassed: 10,
        bestTotal: 20,
        bestModel: "codex/gpt-5.3-codex",
        medianPassRate: 0.5,
        runCount: 3,
        totalTokens: 12345,
        perModelCounts: {},
      },
    ]);
  });
});
