/**
 * GET /api/trajectories/[id]/code-history
 *
 * Returns code snapshots for each commit in the trajectory.
 * The response is a map from commit index (number) to CodeSnapshot.
 * Returns 404 if code_snapshots.parquet does not exist for this trajectory.
 */

import { NextRequest, NextResponse } from "next/server";
import { getCodeHistory } from "@/lib/server/data";

type RouteParams = { params: Promise<{ id: string }> };

export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const { id } = await params;
    const codeHistory = await getCodeHistory(id);

    if (codeHistory === undefined) {
      return NextResponse.json(
        { error: "Code history not available for this trajectory" },
        { status: 404 },
      );
    }

    return NextResponse.json(codeHistory);
  } catch (error) {
    console.error("GET /api/trajectories/[id]/code-history error:", error);
    return NextResponse.json(
      { error: "Failed to fetch code history" },
      { status: 500 },
    );
  }
}
