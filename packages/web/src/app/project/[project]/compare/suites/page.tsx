"use client";

import { useCompare } from "@/components/compare/compare-context";
import { SuiteBreakdown } from "@/components/compare/suite-breakdown";

export default function ProjectSuitesPage() {
  const { selectedTraces, colorIndices, selectedSuites } = useCompare();

  return (
    <SuiteBreakdown
      traces={selectedTraces}
      colorIndices={colorIndices}
      suites={selectedSuites}
    />
  );
}
