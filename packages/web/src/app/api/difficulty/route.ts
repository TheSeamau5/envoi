/**
 * GET /api/difficulty — Difficulty heatmap data.
 * Returns per-(category, model) pass rates.
 */

import { NextRequest, NextResponse } from "next/server";
import { getDifficultyData } from "@/lib/server/data";

export async function GET(_request: NextRequest) {
  try {
    const data = await getDifficultyData();
    return NextResponse.json(data);
  } catch (error) {
    console.error("GET /api/difficulty error:", error);
    return NextResponse.json(
      { error: "Failed to fetch difficulty data" },
      { status: 500 },
    );
  }
}
