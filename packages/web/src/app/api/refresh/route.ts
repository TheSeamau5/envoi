/**
 * POST /api/refresh
 *
 * Triggers a data re-sync from S3 and reloads summary tables into DuckDB.
 * Also clears the in-memory API response cache so subsequent requests
 * see fresh data.
 */

import { NextResponse } from "next/server";
import { refreshData } from "@/lib/server/db";
import { clearCache } from "@/lib/server/cache";

export async function POST() {
  try {
    await refreshData();
    clearCache();
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("POST /api/refresh error:", error);
    return NextResponse.json({ error: "Refresh failed" }, { status: 500 });
  }
}
