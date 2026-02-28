/**
 * GET /api/trajectories/[id]
 *
 * Returns full trajectory data for the detail page.
 */

import { NextRequest, NextResponse } from "next/server";
import { getTrajectoryById } from "@/lib/server/data";

type RouteParams = { params: Promise<{ id: string }> };

export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const trajectory = await getTrajectoryById(id);

    if (!trajectory) {
      return NextResponse.json(
        { error: "Trajectory not found" },
        { status: 404 },
      );
    }

    return NextResponse.json(trajectory);
  } catch (error) {
    console.error("GET /api/trajectories/[id] error:", error);
    return NextResponse.json(
      { error: "Failed to fetch trajectory" },
      { status: 500 },
    );
  }
}
