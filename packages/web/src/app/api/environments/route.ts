/**
 * GET /api/environments
 *
 * Returns distinct environments found in the data with metadata.
 */

import { NextResponse } from "next/server";
import { getEnvironments } from "@/lib/server/data";

export async function GET() {
  try {
    const environments = await getEnvironments();
    return NextResponse.json(environments);
  } catch (error) {
    console.error("GET /api/environments error:", error);
    return NextResponse.json(
      { error: "Failed to fetch environments" },
      { status: 500 },
    );
  }
}
