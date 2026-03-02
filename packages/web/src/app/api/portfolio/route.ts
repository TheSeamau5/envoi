/**
 * GET /api/portfolio — Portfolio dashboard data.
 * Returns per-model rankings across environments.
 */

import { NextRequest, NextResponse } from "next/server";
import { getPortfolioData } from "@/lib/server/data";

export async function GET(_request: NextRequest) {
  try {
    const data = await getPortfolioData();
    return NextResponse.json(data);
  } catch (error) {
    console.error("GET /api/portfolio error:", error);
    return NextResponse.json(
      { error: "Failed to fetch portfolio data" },
      { status: 500 },
    );
  }
}
