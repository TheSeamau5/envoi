"use client";

import { useCompare } from "@/components/compare/compare-context";
import { MilestoneTable } from "@/components/compare/milestone-table";

export default function ProjectMilestonesPage() {
  const { selectedTraces, colorIndices, selectedSuites } = useCompare();

  return (
    <MilestoneTable
      traces={selectedTraces}
      colorIndices={colorIndices}
      suites={selectedSuites}
    />
  );
}
