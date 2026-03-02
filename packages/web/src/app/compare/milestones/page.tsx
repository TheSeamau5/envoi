/**
 * Milestone Divergence sub-route — renders the MilestoneTable visualization.
 * Consumes shared state from CompareProvider via useCompare().
 */

"use client";

import { useCompare } from "@/components/compare/compare-context";
import { MilestoneTable } from "@/components/compare/milestone-table";

export default function MilestonesPage() {
  const { selectedTraces, colorIndices, selectedSuites } = useCompare();

  return (
    <MilestoneTable
      traces={selectedTraces}
      colorIndices={colorIndices}
      suites={selectedSuites}
    />
  );
}
