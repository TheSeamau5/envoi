import type { Commit, DifficultyCell, Trajectory } from "@/lib/types";

/** A sampled point on the setup compare progress curve. */
export type SetupCompareCurvePoint = {
  minutes: number;
  passedPct: number;
};

function getLastCommit(trace: Trajectory): Commit | undefined {
  return trace.commits[trace.commits.length - 1];
}

function getBestCommit(trace: Trajectory): Commit | undefined {
  return trace.commits.reduce<Commit | undefined>((bestCommit, commit) => {
    if (!bestCommit) {
      return commit;
    }
    if (commit.totalPassed > bestCommit.totalPassed) {
      return commit;
    }
    if (commit.totalPassed < bestCommit.totalPassed) {
      return bestCommit;
    }
    if (commit.minutesElapsed < bestCommit.minutesElapsed) {
      return commit;
    }
    return bestCommit;
  }, undefined);
}

function buildCurvePoint(
  trace: Trajectory,
  targetMinutes: number,
): SetupCompareCurvePoint {
  const eligible = trace.commits.filter(
    (commit) => commit.minutesElapsed <= targetMinutes,
  );
  const lastEligible = eligible[eligible.length - 1];
  const passed = lastEligible?.totalPassed ?? 0;

  return {
    minutes: targetMinutes,
    passedPct: trace.totalTests > 0 ? (passed / trace.totalTests) * 100 : 0,
  };
}

/** Return the highest passed-test count reached by a trajectory. */
export function getTraceBestPassed(trace: Trajectory): number {
  const bestCommit = getBestCommit(trace);
  return Math.max(trace.finalPassed, bestCommit?.totalPassed ?? 0);
}

/** Return the peak pass percentage reached by a trajectory. */
export function getTraceBestPercent(trace: Trajectory): number {
  if (trace.totalTests <= 0) {
    return 0;
  }
  return (getTraceBestPassed(trace) / trace.totalTests) * 100;
}

/** Return the elapsed minutes when a trajectory first reached its peak score. */
export function getTraceBestMinutes(trace: Trajectory): number {
  const bestCommit = getBestCommit(trace);
  if (bestCommit) {
    return bestCommit.minutesElapsed;
  }
  return getLastCommit(trace)?.minutesElapsed ?? 0;
}

function collectEnvironmentSuiteTotals(
  traces: Trajectory[],
): Map<string, Map<string, number>> {
  const envSuites = new Map<string, Map<string, number>>();

  for (const trace of traces) {
    const environment = trace.environment || "unknown";
    let suiteTotals = envSuites.get(environment);
    if (!suiteTotals) {
      suiteTotals = new Map<string, number>();
      envSuites.set(environment, suiteTotals);
    }

    for (const suite of trace.suites ?? []) {
      const currentTotal = suiteTotals.get(suite.name) ?? 0;
      if (suite.total > currentTotal) {
        suiteTotals.set(suite.name, suite.total);
      }
    }
  }

  return envSuites;
}

/** Pick the trace with the highest peak passed count for setup comparison. */
export function pickBestTrace(traces: Trajectory[]): Trajectory | undefined {
  return traces.reduce<Trajectory | undefined>((bestTrace, trace) => {
    if (!bestTrace) {
      return trace;
    }

    const tracePassed = getTraceBestPassed(trace);
    const bestPassed = getTraceBestPassed(bestTrace);
    if (tracePassed > bestPassed) {
      return trace;
    }
    if (tracePassed < bestPassed) {
      return bestTrace;
    }

    const tracePct = getTraceBestPercent(trace);
    const bestPct = getTraceBestPercent(bestTrace);
    if (tracePct > bestPct) {
      return trace;
    }
    if (tracePct < bestPct) {
      return bestTrace;
    }

    const traceMinutes = getTraceBestMinutes(trace);
    const bestMinutes = getTraceBestMinutes(bestTrace);
    if (traceMinutes < bestMinutes) {
      return trace;
    }
    if (traceMinutes > bestMinutes) {
      return bestTrace;
    }

    return trace.id.localeCompare(bestTrace.id) < 0 ? trace : bestTrace;
  }, undefined);
}

/** Return the suite score from the trajectory's best-scoring commit. */
export function getTraceBestSuitePassed(
  trace: Trajectory,
  suiteName: string,
): number {
  const bestCommit = getBestCommit(trace);
  if (!bestCommit) {
    return 0;
  }
  return bestCommit.suiteState[suiteName] ?? 0;
}

/** Build difficulty heatmap cells using the same best-trace semantics as setups. */
export function buildDifficultyCells(traces: Trajectory[]): DifficultyCell[] {
  const envSuites = collectEnvironmentSuiteTotals(traces);
  const groups = new Map<string, Trajectory[]>();

  for (const trace of traces) {
    const environment = trace.environment || "unknown";
    const key = `${environment}\u0000${trace.model}`;
    const existing = groups.get(key);
    if (existing) {
      existing.push(trace);
    } else {
      groups.set(key, [trace]);
    }
  }

  const cells: DifficultyCell[] = [];
  for (const [key, groupTraces] of groups.entries()) {
    const [environment, model] = key.split("\u0000");
    if (!environment || !model) {
      continue;
    }
    const suiteTotals = envSuites.get(environment);
    const bestTrace = pickBestTrace(groupTraces);
    if (!suiteTotals || !bestTrace) {
      continue;
    }

    for (const [suiteName, total] of suiteTotals.entries()) {
      const passed = getTraceBestSuitePassed(bestTrace, suiteName);
      cells.push({
        environment,
        category: suiteName,
        model,
        passRate: total > 0 ? passed / total : 0,
        attempts: groupTraces.length,
      });
    }
  }

  return cells.sort(
    (left, right) =>
      left.environment.localeCompare(right.environment) ||
      left.category.localeCompare(right.category) ||
      left.model.localeCompare(right.model),
  );
}

/** Build the selected trace's actual progress curve up to its best-scoring commit. */
export function computeBestCurve(
  trace: Trajectory | undefined,
  maxDuration: number,
): SetupCompareCurvePoint[] {
  if (!trace) {
    return [];
  }

  const bestCommit = getBestCommit(trace);
  if (!bestCommit) {
    return [];
  }

  const numSamples = 48;
  const stepMinutes = maxDuration / numSamples;
  const sampledMinutes = Array.from(
    { length: numSamples + 1 },
    (_, sampleIdx) => {
      return sampleIdx * stepMinutes;
    },
  ).filter((targetMinutes) => targetMinutes <= bestCommit.minutesElapsed);

  const lastSample = sampledMinutes[sampledMinutes.length - 1];
  if (lastSample !== bestCommit.minutesElapsed) {
    sampledMinutes.push(bestCommit.minutesElapsed);
  }

  return sampledMinutes.map((targetMinutes) => {
    return buildCurvePoint(trace, targetMinutes);
  });
}
