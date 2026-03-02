/**
 * GET /api/trajectories/[id]/waste — Waste analysis for a trajectory.
 * Returns per-category waste breakdown.
 */

import { NextRequest, NextResponse } from "next/server";
import { getWasteAnalysis } from "@/lib/server/data";

type RouteParams = {
  params: Promise<{ id: string }>;
};

export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const data = await getWasteAnalysis(id);
    return NextResponse.json(data);
  } catch (error) {
    console.error("GET /api/trajectories/[id]/waste error:", error);
    return NextResponse.json(
      { error: "Failed to fetch waste analysis" },
      { status: 500 },
    );
  }
}
