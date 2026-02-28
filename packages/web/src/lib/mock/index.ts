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
  NATURAL_LANGS,
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

/** Code snippet lines for mock file_read output */
const CODE_LINES = [
  "fn main() {",
  "    let input = std::env::args().nth(1).unwrap();",
  "    let tokens = lexer::tokenize(&input);",
  "    let ast = parser::parse(&tokens)?;",
  "    let ir = codegen::lower(&ast);",
  "    codegen::emit_x86_64(&ir, &mut output);",
  "    match token {",
  "        Token::Ident(name) => resolve_symbol(name),",
  "        Token::IntLit(val) => emit_immediate(val),",
  "        _ => return Err(\"unexpected token\"),",
  "    }",
  "    let aligned = (stack_depth + 15) & !15;",
  "    emit_sub_rsp(aligned - stack_depth);",
  "    emit_call(func_label);",
  "    emit_add_rsp(aligned - stack_depth);",
] as const;

/** Reasoning paragraph templates for multi-paragraph thinking traces */
const REASONING_PARAGRAPHS = [
  "Looking at the error output, the generated assembly is missing the proper stack alignment before the function call. The x86_64 ABI requires 16-byte stack alignment at the point of a CALL instruction.",
  "I need to track the current stack depth through the code generator, insert alignment padding before each call, and remove the padding after the call returns. This is a fundamental issue.",
  "The failing test case calls a variadic function (printf), which is particularly sensitive to stack alignment on x86_64. The System V ABI mandates that AL contains the number of vector registers used.",
  "Tracing through the generated code: we push rbp (8 bytes), then allocate local variables. If locals use an odd number of 8-byte slots, the stack will be misaligned at the next call site.",
  "The type checker is not propagating const qualifiers through pointer dereferences. This causes the codegen to emit unnecessary loads and stores, which in turn breaks the alias analysis.",
  "I should check if the register allocator is spilling correctly around function calls. The caller-saved registers (rax, rcx, rdx, rsi, rdi, r8-r11) need to be preserved if they hold live values.",
  "The issue might be in how we handle struct returns. The ABI says structs larger than 16 bytes are returned via a hidden pointer parameter, which shifts all other arguments by one register.",
  "Let me review the calling convention: integer/pointer args go in rdi, rsi, rdx, rcx, r8, r9 in order. Floating point args go in xmm0-xmm7. Additional args go on the stack right-to-left.",
] as const;

/** Plan templates for reasoning steps */
const PLAN_TEMPLATES = [
  "1. Fix stack alignment tracking in codegen.rs\n2. Add padding insertion before CALL instructions\n3. Update register allocator to account for alignment slots\n4. Re-run basics and wacct suites to verify\n5. Check for regressions in c_testsuite",
  "1. Implement const qualifier propagation in type checker\n2. Update AST to carry const info through pointer types\n3. Fix codegen to respect const for alias analysis\n4. Run full test suite",
  "1. Add struct return ABI handling for types > 16 bytes\n2. Implement hidden pointer parameter insertion\n3. Update calling convention logic for shifted arguments\n4. Test with struct-heavy test cases\n5. Verify no regressions in basic function calls",
  "1. Review current register allocation strategy\n2. Implement caller-save spill around call sites\n3. Add callee-save register restore in function epilogue\n4. Run torture tests to verify correctness",
  "1. Parse switch statement syntax\n2. Implement jump table generation for dense cases\n3. Add fallthrough semantics\n4. Handle default case\n5. Test with basics and wacct suites",
] as const;

/** Build output templates */
const BUILD_OUTPUTS = [
  "   Compiling cc v0.1.0 (/workspace)\n    Finished dev [unoptimized + debuginfo] target(s) in 2.34s",
  "   Compiling cc v0.1.0 (/workspace)\nwarning: unused variable `temp_reg` in codegen.rs:142\n    Finished dev [unoptimized + debuginfo] target(s) in 1.87s",
  "   Compiling cc v0.1.0 (/workspace)\n    Finished dev [unoptimized + debuginfo] target(s) in 3.12s\n\nRunning target/debug/cc",
] as const;

/** Build error templates */
const BUILD_ERRORS = [
  "error[E0308]: mismatched types\n  --> src/codegen.rs:89:20\n   |\n89 |     let offset: u64 = calculate_offset(node);\n   |                 ^^^ expected `u64`, found `i64`",
  "error[E0502]: cannot borrow `self.registers` as mutable\n  --> src/regalloc.rs:45:9\n   |\n44 |     let current = &self.registers[idx];\n45 |     self.registers.push(new_reg);\n   |     ^^^^^^^^^^^^^^ cannot borrow as mutable",
  "error: linking failed with exit code 1\nnote: /usr/bin/ld: undefined reference to `__stack_chk_fail'\ncollect2: error: ld returned 1 exit status",
] as const;

/** Generate steps (agent actions) for a single commit */
function generateSteps(rng: SeededRng, phase: number): Step[] {
  const numSteps = 3 + Math.floor(rng.next() * 12);
  const steps: Step[] = [];
  const stageLabel = phase < 0.3 ? "lexer/parser" : phase < 0.6 ? "type checker" : "code generation";

  for (let stepIdx = 0; stepIdx < numSteps; stepIdx++) {
    const roll = rng.next();

    if (stepIdx === 0) {
      // First step is always reasoning with full thinking trace
      const summary = rng.pick(REASONING_SUMMARIES);
      const numParagraphs = 2 + Math.floor(rng.next() * 3);
      const paragraphs = Array.from({ length: numParagraphs }, () => rng.pick(REASONING_PARAGRAPHS));
      const reasoningContent = `${summary}\n\nLet me trace through the failing test case step by step...\n\nThe issue appears to be in the ${stageLabel} stage.\n\n${paragraphs.join("\n\n")}`;
      const hasPlan = rng.next() < 0.4;
      const planContent = hasPlan ? rng.pick(PLAN_TEMPLATES) : undefined;

      steps.push({
        type: "reasoning",
        summary,
        detail: `${summary}\n\nLet me trace through the failing test case step by step...\n\nThe issue appears to be in the ${stageLabel} stage.`,
        index: stepIdx,
        durationMs: rng.nextInt(500, 30000),
        tokensUsed: rng.nextInt(20000, 80000),
        reasoningContent,
        planContent,
      });
    } else if (roll < 0.25) {
      // Secondary reasoning
      const summary = rng.pick(REASONING_SECONDARY);
      const paragraph = rng.pick(REASONING_PARAGRAPHS);
      steps.push({
        type: "reasoning",
        summary,
        detail: summary,
        index: stepIdx,
        durationMs: rng.nextInt(500, 15000),
        tokensUsed: rng.nextInt(5000, 40000),
        reasoningContent: `${summary}\n\n${paragraph}`,
      });
    } else if (roll < 0.45) {
      // File read
      const file = rng.pick(SOURCE_FILES);
      const numLines = rng.nextInt(5, 15);
      const codeSnippet = Array.from({ length: numLines }, () => rng.pick(CODE_LINES)).join("\n");
      steps.push({
        type: "file_read",
        summary: `Read ${file}`,
        detail: `Read ${file}`,
        index: stepIdx,
        durationMs: rng.nextInt(100, 2000),
        tokensUsed: rng.nextInt(2000, 10000),
        toolInput: JSON.stringify({ path: file, lines: `1-${numLines * 10}` }, undefined, 2),
        toolOutput: `// ${file}\n${codeSnippet}`,
      });
    } else if (roll < 0.65) {
      // File write
      const file = rng.pick(SOURCE_FILES);
      const lineCount = Math.floor(rng.next() * 30) + 3;
      steps.push({
        type: "file_write",
        summary: `Edit ${file} (+${lineCount} lines)`,
        detail: `Edit ${file} (+${lineCount} lines)`,
        index: stepIdx,
        durationMs: rng.nextInt(200, 5000),
        tokensUsed: rng.nextInt(3000, 20000),
        toolInput: JSON.stringify({ path: file, operation: "insert", startLine: rng.nextInt(10, 200), lineCount }, undefined, 2),
        toolOutput: `Successfully wrote ${lineCount} lines to ${file}`,
      });
    } else if (roll < 0.8) {
      // Tool call (e.g., cargo build)
      const summary = rng.pick(TOOL_CALLS);
      const isToolError = rng.next() < 0.15;
      const toolOutput = isToolError ? rng.pick(BUILD_ERRORS) : rng.pick(BUILD_OUTPUTS);
      steps.push({
        type: "tool_call",
        summary,
        detail: summary,
        index: stepIdx,
        durationMs: rng.nextInt(1000, 30000),
        tokensUsed: rng.nextInt(5000, 30000),
        toolInput: JSON.stringify({ command: summary, cwd: "/workspace", timeout: rng.nextInt(10, 60) * 1000 }, undefined, 2),
        toolOutput,
        isError: isToolError,
        errorMessage: isToolError ? toolOutput.split("\n")[0] : undefined,
      });
    } else if (roll < 0.9) {
      // Test run
      const suiteName = rng.pick(TEST_SUITE_NAMES);
      const totalTests = rng.nextInt(10, 200);
      const passed = rng.nextInt(Math.floor(totalTests * 0.5), totalTests);
      const failed = totalTests - passed;
      const isTestError = failed > totalTests * 0.3;
      const failedLines = failed > 0
        ? `\n\nFailed tests:\n${Array.from({ length: Math.min(failed, 5) }, () => `  FAIL: test_${rng.nextInt(1, 500).toString().padStart(3, "0")} — ${rng.pick(ERROR_MESSAGES)}`).join("\n")}`
        : "";
      steps.push({
        type: "test_run",
        summary: `Run ${suiteName} suite`,
        detail: `Run ${suiteName} suite`,
        index: stepIdx,
        durationMs: rng.nextInt(2000, 60000),
        tokensUsed: rng.nextInt(1000, 5000),
        toolInput: JSON.stringify({ suite: suiteName, filter: "*" }, undefined, 2),
        toolOutput: `Running ${suiteName} suite...\n\n${passed} passed, ${failed} failed, 0 skipped${failedLines}`,
        isError: isTestError,
        errorMessage: isTestError ? `${failed}/${totalTests} tests failed` : undefined,
      });
    } else {
      // MCP call
      const suite = rng.pick(TEST_SUITE_NAMES);
      const summary = suite === "wacct"
        ? `envoi.test(wacct/ch_${rng.nextInt(1, 20)})`
        : `envoi.test(${suite})`;
      const passed = rng.nextInt(0, 50);
      const failed = rng.nextInt(0, 10);
      const isMcpError = rng.next() < 0.1;
      steps.push({
        type: "mcp_call",
        summary,
        detail: summary,
        index: stepIdx,
        durationMs: rng.nextInt(3000, 45000),
        tokensUsed: rng.nextInt(2000, 15000),
        toolInput: JSON.stringify({ server: "envoi", tool: "test", args: { suite, timeout: 30000 } }, undefined, 2),
        toolOutput: isMcpError
          ? `Error: MCP server timeout after 30s\nThe test execution exceeded the maximum allowed time.`
          : `Test results for ${suite}:\n  Passed: ${passed}\n  Failed: ${failed}\n  Total: ${passed + failed}`,
        isError: isMcpError,
        errorMessage: isMcpError ? "MCP server timeout after 30s" : undefined,
      });
    }
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
  const baseDate = new Date("2025-01-15T10:00:00Z");
  const dayOffset = Math.floor(rng.next() * 60) - 30;
  const hourOffset = Math.floor(rng.next() * 14);
  const minuteOffset = Math.floor(rng.next() * 60);
  const startDate = new Date(
    baseDate.getTime() + dayOffset * 86400000 + hourOffset * 3600000 + minuteOffset * 60000,
  );

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
    suites: SUITES,
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
        lang: rng.pick(NATURAL_LANGS),
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
