"use client";

import { useCompare } from "@/components/compare/compare-context";
import { ProgressCurves } from "@/components/compare/progress-curves";

export default function ProjectCurvesPage() {
  const { selectedTraces, colorIndices } = useCompare();

  return <ProgressCurves traces={selectedTraces} colorIndices={colorIndices} />;
}
