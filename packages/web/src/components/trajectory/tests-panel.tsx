/**
 * Tests & Metrics panel — shows per-suite test results and broken tests
 * for the currently selected commit.
 * Client component — depends on selected commit state.
 */

"use client";

import type { Commit, Suite } from "@/lib/types";
import { SUITES as DEFAULT_SUITES, TOTAL_TESTS as DEFAULT_TOTAL_TESTS } from "@/lib/constants";
import { SUITE_COLORS, T } from "@/lib/tokens";
import { formatPercent } from "@/lib/utils";

type TestsPanelProps = {
  commit: Commit;
  suites?: Suite[];
  totalTests?: number;
};

export function TestsPanel({ commit, suites, totalTests }: TestsPanelProps) {
  const effectiveSuites = suites ?? DEFAULT_SUITES;
  const effectiveTotal = totalTests ?? DEFAULT_TOTAL_TESTS;
  const overallPercent = formatPercent(commit.totalPassed, effectiveTotal);

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      {/* Overall summary */}
      <div className="border-b border-envoi-border px-[14px] py-[12px]">
        <div className="flex items-baseline gap-2">
          <span className="text-[18px] font-bold text-envoi-text">
            {commit.totalPassed}
          </span>
          <span className="text-[13px] text-envoi-text-dim">
            / {effectiveTotal} passed ({overallPercent})
          </span>
        </div>
        <div className="mt-[6px] flex items-center gap-2">
          <span
            className="text-[13px] font-semibold"
            style={{
              color:
                commit.delta > 0
                  ? T.greenDark
                  : commit.delta < 0
                    ? T.redDark
                    : T.textMuted,
            }}
          >
            {commit.delta > 0 ? `+${commit.delta}` : commit.delta === 0 ? "±0" : `${commit.delta}`}
          </span>
          <span className="text-[13px] text-envoi-text-dim">
            since previous commit
          </span>
        </div>
      </div>

      {/* Per-suite breakdown */}
      <div className="border-b border-envoi-border px-[14px] py-[10px]">
        <span className="text-[13px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Suites
        </span>
        <div className="mt-[8px] space-y-[10px]">
          {effectiveSuites.map((suite) => {
            const passed = commit.suiteState[suite.name] ?? 0;
            const ratio = passed / suite.total;
            const suiteColor = SUITE_COLORS[suite.name];
            return (
              <div key={suite.name}>
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-semibold text-envoi-text">
                    {suite.name}
                  </span>
                  <span className="text-[12px] text-envoi-text-dim">
                    {passed} / {suite.total}
                  </span>
                </div>
                <div
                  className="mt-[3px] h-[6px] rounded-full"
                  style={{ background: suiteColor?.bg ?? T.borderLight }}
                >
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${(ratio * 100).toFixed(1)}%`,
                      background: suiteColor?.color ?? T.textDim,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Broken tests */}
      {commit.feedback.newlyBroken > 0 && (
        <div className="px-[14px] py-[10px]">
          <span className="text-[13px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Regressions ({commit.feedback.newlyBroken})
          </span>
          {commit.feedback.brokenTests.length > 0 ? (
            <div className="mt-[8px] space-y-[6px]">
              {commit.feedback.brokenTests.map((brokenTest, testIndex) => (
                <div
                  key={`${brokenTest.suite}-${brokenTest.testId}-${testIndex}`}
                  className="rounded border border-envoi-border-light px-[10px] py-[8px]"
                  style={{ background: T.redBg }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="rounded-[2px] px-[5px] py-[1px] text-[13px] font-medium"
                      style={{
                        color: SUITE_COLORS[brokenTest.suite]?.color ?? T.textMuted,
                        background: SUITE_COLORS[brokenTest.suite]?.bg ?? T.borderLight,
                      }}
                    >
                      {brokenTest.suite}
                    </span>
                    <span className="text-[12px] font-semibold text-envoi-text">
                      {brokenTest.testId}
                    </span>
                  </div>
                  <div className="mt-[4px] text-[13px]" style={{ color: T.redDark }}>
                    {brokenTest.error}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-[8px] text-[12px] text-envoi-text-dim">
              {commit.feedback.newlyBroken} test{commit.feedback.newlyBroken > 1 ? "s" : ""} regressed in this commit.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
