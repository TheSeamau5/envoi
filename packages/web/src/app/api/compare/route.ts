/**
 * GET /api/compare
 *
 * Returns trajectory data for the compare page.
 * Query params: ?ids=traj1,traj2,traj3 or ?environment=c_compiler
 */

import { NextRequest, NextResponse } from "next/server";
import { getCompareTrajectories } from "@/lib/server/data";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const idsParam = searchParams.get("ids");
    const environment = searchParams.get("environment") ?? undefined;

    const ids = idsParam
      ? idsParam.split(",").filter(Boolean)
      : undefined;

    const trajectories = await getCompareTrajectories({
      ids,
      environment,
    });

    return NextResponse.json(trajectories);
  } catch (error) {
    console.error("GET /api/compare error:", error);
    return NextResponse.json(
      { error: "Failed to fetch compare data" },
      { status: 500 },
    );
  }
}
