/**
 * GET /api/trajectories/[id]/evaluations
 *
 * Returns evaluation data for progress curves and suite breakdown.
 */

import { NextRequest, NextResponse } from "next/server";
import { getTrajectoryEvaluations } from "@/lib/server/data";

type RouteParams = { params: Promise<{ id: string }> };

export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const evaluations = await getTrajectoryEvaluations(id);

    return NextResponse.json(evaluations);
  } catch (error) {
    console.error("GET /api/trajectories/[id]/evaluations error:", error);
    return NextResponse.json(
      { error: "Failed to fetch evaluations" },
      { status: 500 },
    );
  }
}
