/**
 * GET /api/portfolio — Portfolio dashboard data.
 * Returns per-model rankings, environment summaries, and Pareto frontier points.
 */

import { NextRequest, NextResponse } from "next/server";
import {
  getPortfolioData,
  getPortfolioEnvironmentData,
  getParetoData,
} from "@/lib/server/data";

/** GET handler for full portfolio dashboard payload */
export async function GET(_request: NextRequest) {
  try {
    const [rows, environmentRows, paretoPoints] = await Promise.all([
      getPortfolioData(),
      getPortfolioEnvironmentData(),
      getParetoData(),
    ]);

    return NextResponse.json({ rows, environmentRows, paretoPoints });
  } catch (error) {
    console.error("GET /api/portfolio error:", error);
    return NextResponse.json(
      { error: "Failed to fetch portfolio data" },
      { status: 500 },
    );
  }
}
