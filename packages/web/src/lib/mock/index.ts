/**
 * Mock data generation for the Envoi dashboard.
 * Generates 30 deterministic trajectories across 6 models with realistic
 * C compiler agent behavior patterns.
 *
 * All functions are pure and deterministic — same seed = same output.
 */

import type {
  Trajectory,
  Commit,
  Step,
  Evaluation,
  BrokenTest,
  ChangedFile,
  CodeSnapshot,
  SuiteState,
  TrajectoryParams,
} from "../types";
import { SUITES, TOTAL_TESTS } from "../constants";
import { createRng, generateHash, type SeededRng } from "./rng";
import { CODE_TEMPLATES, LINE_POOLS, NEW_FILE_TEMPLATES } from "./code-templates";
import {
  MODEL_CONFIGS,
  IMPL_LANGS,
  SANDBOXES,
  AGENTS,
  MILESTONE_STARTS,
  ERROR_MESSAGES,
  REASONING_SUMMARIES,
  REASONING_SECONDARY,
  SOURCE_FILES,
  TOOL_CALLS,
  TEST_SUITE_NAMES,
} from "./constants";

/** Build code evolution snapshots for a trajectory */
function buildCodeEvolution(numCommits: number, rng: SeededRng): CodeSnapshot[] {
  const allFiles = Object.keys(CODE_TEMPLATES);
  const snapshots: CodeSnapshot[] = [];
  const currentCode: Record<string, string[]> = {};

  for (const file of allFiles) {
    currentCode[file] = [...(CODE_TEMPLATES[file] ?? [])];
  }

  for (let commitIdx = 0; commitIdx < numCommits; commitIdx++) {
    const phase = commitIdx / numCommits;
    const phaseKey: "early" | "mid" | "late" =
      phase < 0.33 ? "early" : phase < 0.66 ? "mid" : "late";
    const fileSnapshot: CodeSnapshot = {};
    const numFilesToTouch = 1 + Math.floor(rng.next() * 3);
    const touchedFiles = new Set<string>();

    for (let fileIdx = 0; fileIdx < numFilesToTouch; fileIdx++) {
      touchedFiles.add(rng.pick(allFiles));
    }

    for (const file of allFiles) {
      const added: number[] = [];
      if (touchedFiles.has(file)) {
        const pool = LINE_POOLS[file]?.[phaseKey] ?? [];
        if (pool.length > 0) {
          const numToInsert = Math.min(1 + Math.floor(rng.next() * 4), pool.length);
          const startIdx = Math.floor(rng.next() * pool.length);
          const lines = pool.slice(startIdx, startIdx + numToInsert);
          const fileLines = currentCode[file]!;
          const insertAt = Math.max(1, fileLines.length - 1 - Math.floor(rng.next() * 3));
          fileLines.splice(insertAt, 0, ...lines);
          for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
            added.push(insertAt + lineIdx);
          }
          if (rng.next() > 0.4 && fileLines.length > 5) {
            const modIdx = 2 + Math.floor(rng.next() * (fileLines.length - 4));
            if (!added.includes(modIdx)) {
              fileLines[modIdx] = fileLines[modIdx] + " // updated";
              added.push(modIdx);
            }
          }
        }
      }
      fileSnapshot[file] = {
        lines: [...(currentCode[file] ?? [])],
        added,
        touched: touchedFiles.has(file),
      };
    }

    // Introduce new files at specific phases
    for (const [filePath, template] of Object.entries(NEW_FILE_TEMPLATES)) {
      if (commitIdx === Math.floor(numCommits * template.phase)) {
        currentCode[filePath] = [...template.content];
        fileSnapshot[filePath] = {
          lines: [...template.content],
          added: Array.from({ length: template.content.length }, (_, idx) => idx),
          touched: true,
          isNew: true,
        };
      }
    }

    snapshots.push(fileSnapshot);
  }

  return snapshots;
}

/** Generate steps (agent actions) for a single commit */
function generateSteps(rng: SeededRng, phase: number): Step[] {
  const numSteps = 3 + Math.floor(rng.next() * 12);
  const steps: Step[] = [];

  for (let stepIdx = 0; stepIdx < numSteps; stepIdx++) {
    const roll = rng.next();
    let type: Step["type"];
    let summary: string;
    let detail: string;

    if (stepIdx === 0) {
      type = "reasoning";
      summary = rng.pick(REASONING_SUMMARIES);
      detail = `${summary}\n\nLet me trace through the failing test case step by step...\n\nThe issue appears to be in the ${phase < 0.3 ? "lexer/parser" : phase < 0.6 ? "type checker" : "code generation"} stage.`;
    } else if (roll < 0.25) {
      type = "reasoning";
      summary = rng.pick(REASONING_SECONDARY);
      detail = summary;
    } else if (roll < 0.45) {
      type = "file_read";
      const file = rng.pick(SOURCE_FILES);
      summary = `Read ${file}`;
      detail = summary;
    } else if (roll < 0.65) {
      type = "file_write";
      const file = rng.pick(SOURCE_FILES);
      const lineCount = Math.floor(rng.next() * 30) + 3;
      summary = `Edit ${file} (+${lineCount} lines)`;
      detail = summary;
    } else if (roll < 0.8) {
      type = "tool_call";
      summary = rng.pick(TOOL_CALLS);
      detail = summary;
    } else if (roll < 0.9) {
      type = "test_run";
      summary = `Run ${rng.pick(TEST_SUITE_NAMES)} suite`;
      detail = summary;
    } else {
      type = "mcp_call";
      const suite = rng.pick(TEST_SUITE_NAMES);
      summary =
        suite === "wacct"
          ? `envoi.test(wacct/ch_${rng.nextInt(1, 20)})`
          : `envoi.test(${suite})`;
      detail = summary;
    }

    steps.push({
      type,
      summary,
      detail,
      index: stepIdx,
      durationMs: rng.nextInt(500, 30000),
      tokensUsed: rng.nextInt(5000, 80000),
    });
  }

  return steps;
}

/** Generate broken test entries for a commit */
function generateBrokenTests(rng: SeededRng, count: number): BrokenTest[] {
  return Array.from({ length: count }, () => ({
    suite: rng.pick(TEST_SUITE_NAMES),
    testId: `test_${rng.nextInt(1, 500).toString().padStart(3, "0")}`,
    error: rng.pick(ERROR_MESSAGES),
  }));
}

/** Generate a single trajectory from a seed and model config */
function generateTrajectoryFromConfig(
  seed: number,
  model: string,
  numCommits: number,
  durationMinutes: number,
  speedFactor: number,
  plateauFactor: number,
  regressionProbability: number,
  finalCeiling: number,
  costPerMTok: number,
  totalTokens: number,
  params: TrajectoryParams,
): Trajectory {
  const rng = createRng(seed);
  const codeRng = createRng(seed + 999);
  const codeSnapshots = buildCodeEvolution(numCommits, codeRng);

  const commits: Commit[] = [];
  const suiteState: SuiteState = { basics: 0, wacct: 0, c_testsuite: 0, torture: 0 };
  let totalPassed = 0;
  const timePerCommit = durationMinutes / numCommits;
  const startDate = new Date("2025-01-15T10:00:00Z");

  for (let commitIdx = 0; commitIdx < numCommits; commitIdx++) {
    const phase = commitIdx / numCommits;
    const hash = generateHash(rng);
    const prevTotal = totalPassed;

    // Suite progression with unlocking rules
    // basics unlocks first
    if (phase > 0.05 * speedFactor && suiteState["basics"]! < 35) {
      const gain = Math.min(
        35 - suiteState["basics"]!,
        Math.floor(rng.next() * 8 + 2),
      );
      if (rng.next() > 0.3 * plateauFactor) {
        suiteState["basics"] = Math.min(35, suiteState["basics"]! + gain);
      }
    }

    // wacct unlocks after basics > 15
    if (phase > 0.15 * speedFactor && suiteState["basics"]! > 15) {
      const maxWacct = Math.floor(
        1559 * finalCeiling * Math.min(1, phase / (0.7 * speedFactor)),
      );
      const gain = Math.floor(rng.next() * 45 + 5);
      if (rng.next() > 0.4 * plateauFactor) {
        suiteState["wacct"] = Math.min(maxWacct, suiteState["wacct"]! + gain);
      }
    }

    // c_testsuite unlocks after wacct > 300
    if (phase > 0.3 * speedFactor && suiteState["wacct"]! > 300) {
      const maxCts = Math.floor(
        220 * finalCeiling * Math.min(1, phase / (0.8 * speedFactor)),
      );
      const gain = Math.floor(rng.next() * 12 + 1);
      if (rng.next() > 0.45 * plateauFactor) {
        suiteState["c_testsuite"] = Math.min(maxCts, suiteState["c_testsuite"]! + gain);
      }
    }

    // torture unlocks after c_testsuite > 60
    if (phase > 0.55 * speedFactor && suiteState["c_testsuite"]! > 60) {
      const maxTorture = Math.floor(
        370 * finalCeiling * Math.min(1, phase / (0.9 * speedFactor)),
      );
      const gain = Math.floor(rng.next() * 8 + 1);
      if (rng.next() > 0.5 * plateauFactor) {
        suiteState["torture"] = Math.min(maxTorture, suiteState["torture"]! + gain);
      }
    }

    // Apply regression
    const isRegression =
      phase > 0.2 && phase < 0.8 && rng.next() < regressionProbability;
    if (isRegression) {
      const wacctLoss = Math.floor(rng.next() * 80 + 20);
      suiteState["wacct"] = Math.max(0, suiteState["wacct"]! - wacctLoss);
      if (rng.next() > 0.5) {
        const ctsLoss = Math.floor(rng.next() * 15);
        suiteState["c_testsuite"] = Math.max(0, suiteState["c_testsuite"]! - ctsLoss);
      }
    }

    // Clamp to suite totals
    for (const suite of SUITES) {
      suiteState[suite.name] = Math.min(suite.total, Math.max(0, suiteState[suite.name]!));
    }

    totalPassed = SUITES.reduce(
      (sum, suite) => sum + (suiteState[suite.name] ?? 0),
      0,
    );
    const delta = totalPassed - prevTotal;

    // Milestones
    const prevCommit = commitIdx > 0 ? commits[commitIdx - 1] : undefined;
    const isMilestone =
      (suiteState["basics"] === 35 &&
        (prevCommit?.suiteState["basics"] ?? 0) < 35) ||
      (suiteState["c_testsuite"]! > 200 &&
        (prevCommit?.suiteState["c_testsuite"] ?? 0) <= 200);
    const milestoneLabel =
      suiteState["basics"] === 35 &&
      (prevCommit?.suiteState["basics"] ?? 0) < 35
        ? "basics 100%"
        : isMilestone
          ? "c_testsuite >90%"
          : undefined;

    // Steps
    const steps = generateSteps(rng, phase);

    // Feedback
    const newlyBroken = isRegression
      ? rng.nextInt(1, 8)
      : rng.next() < 0.2
        ? rng.nextInt(1, 3)
        : 0;
    const newlyFixed = Math.max(0, delta) + newlyBroken;
    const brokenTests = generateBrokenTests(rng, newlyBroken);

    // Changed files from code snapshot
    const snapshot = codeSnapshots[commitIdx] ?? {};
    const changedFiles: ChangedFile[] = Object.entries(snapshot)
      .filter(([, fileSnap]) => fileSnap.touched)
      .map(([path, fileSnap]) => ({
        path,
        additions: fileSnap.added.length,
        deletions: Math.floor(rng.next() * 3),
        isNew: fileSnap.isNew ?? false,
      }));

    const feedback: Evaluation = {
      passedDelta: delta,
      newlyBroken,
      newlyFixed,
      brokenTests,
      totalPassed,
      totalFailed: TOTAL_TESTS - totalPassed,
    };

    const minutesElapsed = Math.round((commitIdx + 1) * timePerCommit);
    const timestamp = new Date(
      startDate.getTime() + minutesElapsed * 60 * 1000,
    ).toISOString();
    const commitTokens = Math.floor(totalTokens / numCommits);

    commits.push({
      index: commitIdx,
      hash,
      turn: commitIdx + 1,
      timestamp,
      minutesElapsed,
      suiteState: { ...suiteState },
      totalPassed,
      delta,
      isRegression: delta < 0,
      isMilestone,
      milestoneLabel,
      feedback,
      steps,
      changedFiles,
      codeSnapshot: snapshot,
      phase,
      tokensUsed: commitTokens,
    });
  }

  const finalPassed = commits.length > 0 ? commits[commits.length - 1]!.totalPassed : 0;
  const hours = Math.floor(durationMinutes / 60);
  const mins = durationMinutes % 60;
  const durationStr =
    hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  const cost = (totalTokens / 1_000_000) * costPerMTok;

  return {
    id: `traj-${seed.toString(16).padStart(4, "0")}`,
    model,
    environment: "c_compiler",
    commits,
    totalTests: TOTAL_TESTS,
    startedAt: startDate.toISOString(),
    duration: durationStr,
    totalTokens,
    cost,
    params,
    finalPassed,
  };
}

/** Generate all 30 trajectories across 6 models. Deterministic. */
export function generateAllTrajectories(): Trajectory[] {
  const trajectories: Trajectory[] = [];
  let seedOffset = 42;

  for (const config of MODEL_CONFIGS) {
    for (let traceIdx = 0; traceIdx < config.traceCount; traceIdx++) {
      const seed = seedOffset + traceIdx * 111;
      const rng = createRng(seed);

      const numCommits = rng.nextInt(config.commitRange[0], config.commitRange[1]);
      const durationMinutes = rng.nextInt(
        config.durationRange[0],
        config.durationRange[1],
      );
      const finalCeiling =
        config.finalCeilingRange[0] +
        rng.next() * (config.finalCeilingRange[1] - config.finalCeilingRange[0]);
      const totalTokens = rng.nextInt(config.tokenRange[0], config.tokenRange[1]);

      const params: TrajectoryParams = {
        target: "x86_64-linux",
        implLang: rng.pick(IMPL_LANGS),
        lang: "en",
        milestone: rng.pick(MILESTONE_STARTS),
        sandbox: rng.pick(SANDBOXES),
        agent: rng.pick(AGENTS),
      };

      trajectories.push(
        generateTrajectoryFromConfig(
          seed,
          config.model,
          numCommits,
          durationMinutes,
          config.speedFactor,
          config.plateauFactor,
          config.regressionProbability,
          finalCeiling,
          config.costPerMTok,
          totalTokens,
          params,
        ),
      );

      seedOffset += 100;
    }
  }

  return trajectories;
}

/** Generate a single trajectory by its ID (lookup from all trajectories) */
export function getTrajectoryById(
  trajectoryId: string,
): Trajectory | undefined {
  const all = generateAllTrajectories();
  return all.find((trajectory) => trajectory.id === trajectoryId);
}
