/**
 * GET /api/trajectories
 *
 * Returns trajectory summaries for the list page.
 * Optional query params: ?environment=..., ?model=..., ?limit=..., ?offset=...
 */

import { NextRequest, NextResponse } from "next/server";
import { getAllTrajectories } from "@/lib/server/data";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const environment = searchParams.get("environment") ?? undefined;
    const model = searchParams.get("model") ?? undefined;
    const limit = searchParams.get("limit")
      ? Number(searchParams.get("limit"))
      : undefined;
    const offset = searchParams.get("offset")
      ? Number(searchParams.get("offset"))
      : undefined;

    const trajectories = await getAllTrajectories({
      environment,
      model,
      limit,
      offset,
    });

    return NextResponse.json(trajectories);
  } catch (error) {
    console.error("GET /api/trajectories error:", error);
    return NextResponse.json(
      { error: "Failed to fetch trajectories" },
      { status: 500 },
    );
  }
}
