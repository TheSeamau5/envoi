/**
 * Compare page — server component.
 * Fetches all trajectories from data layer (S3 or mock fallback).
 */

import { getAllTrajectories } from "@/lib/server/data";
import { CompareClient } from "@/components/compare/compare-client";

export default async function ComparePage() {
  const allTraces = await getAllTrajectories();

  return <CompareClient allTraces={allTraces} />;
}
