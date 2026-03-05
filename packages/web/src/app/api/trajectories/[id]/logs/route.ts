/**
 * GET /api/trajectories/[id]/logs
 *
 * Returns structured logs from logs.parquet for a single trajectory.
 * Supports bounded pagination via `fromSeq` and `limit`.
 */

import { NextRequest, NextResponse } from "next/server";
import { getTrajectoryLogsById } from "@/lib/server/data";
import { getProjectFromRequest } from "@/lib/server/project-context";

type RouteParams = { params: Promise<{ id: string }> };

function parsePositiveInt(raw: string | null, fallback: number): number {
  if (!raw) {
    return fallback;
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(0, Math.floor(parsed));
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const project = await getProjectFromRequest(request);
    if (!project) {
      return NextResponse.json(
        { error: "Project not selected" },
        { status: 400 },
      );
    }

    const { id } = await params;
    const fromSeq = parsePositiveInt(
      request.nextUrl.searchParams.get("fromSeq"),
      0,
    );
    const limit = parsePositiveInt(
      request.nextUrl.searchParams.get("limit"),
      2500,
    );
    const fresh = request.nextUrl.searchParams.has("bust");

    const rows = await getTrajectoryLogsById(id, {
      project,
      fromSeq,
      limit,
      fresh,
    });

    if (rows === undefined) {
      return NextResponse.json(
        { error: "Logs not available for this trajectory" },
        { status: 404 },
      );
    }

    return NextResponse.json({ rows });
  } catch (error) {
    console.error("GET /api/trajectories/[id]/logs error:", error);
    return NextResponse.json(
      { error: "Failed to fetch trajectory logs" },
      { status: 500 },
    );
  }
}
