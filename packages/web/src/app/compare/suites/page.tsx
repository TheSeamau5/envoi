/**
 * Suite Breakdown sub-route — renders the SuiteBreakdown visualization.
 * Consumes shared state from CompareProvider via useCompare().
 */

"use client";

import { useCompare } from "@/components/compare/compare-context";
import { SuiteBreakdown } from "@/components/compare/suite-breakdown";

export default function SuitesPage() {
  const { selectedTraces, colorIndices, selectedSuites } = useCompare();

  return (
    <SuiteBreakdown
      traces={selectedTraces}
      colorIndices={colorIndices}
      suites={selectedSuites}
    />
  );
}
