/**
 * Progress Curves sub-route — renders the ProgressCurves visualization.
 * Consumes shared state from CompareProvider via useCompare().
 */

"use client";

import { useCompare } from "@/components/compare/compare-context";
import { ProgressCurves } from "@/components/compare/progress-curves";

export default function CurvesPage() {
  const { selectedTraces, colorIndices } = useCompare();

  return <ProgressCurves traces={selectedTraces} colorIndices={colorIndices} />;
}
