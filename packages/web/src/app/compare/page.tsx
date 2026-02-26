/**
 * Compare page — server component.
 * Generates all 30 mock trajectories and passes them to the client component.
 */

import { generateAllTrajectories } from "@/lib/mock";
import { CompareClient } from "@/components/compare/compare-client";

export default function ComparePage() {
  const allTraces = generateAllTrajectories();

  return <CompareClient allTraces={allTraces} />;
}
