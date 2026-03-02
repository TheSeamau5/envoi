/**
 * POST /api/query — Execute read-only SQL against DuckDB.
 * Rejects mutating statements for safety.
 */

import { NextRequest, NextResponse } from "next/server";
import { executeQuery } from "@/lib/server/data";

/** SQL keywords that indicate a mutating statement */
const MUTATING_KEYWORDS = /\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|TRUNCATE|REPLACE)\b/i;

/** Maximum rows returned to prevent memory issues */
const MAX_ROWS = 1000;

export async function POST(request: NextRequest) {
  try {
    const body: unknown = await request.json();
    if (typeof body !== "object" || body === null || !("sql" in body)) {
      return NextResponse.json(
        { error: "Request body must include a 'sql' field" },
        { status: 400 },
      );
    }

    const sql = String((body as Record<string, unknown>).sql ?? "").trim();
    if (sql.length === 0) {
      return NextResponse.json(
        { error: "SQL query cannot be empty" },
        { status: 400 },
      );
    }

    if (MUTATING_KEYWORDS.test(sql)) {
      return NextResponse.json(
        { error: "Only read-only queries are allowed (SELECT, SHOW, DESCRIBE, PRAGMA)" },
        { status: 400 },
      );
    }

    /** Append LIMIT if not already present */
    const hasLimit = /\bLIMIT\b/i.test(sql);
    const safeSql = hasLimit ? sql : `${sql} LIMIT ${MAX_ROWS}`;

    const result = await executeQuery(safeSql);
    return NextResponse.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Query execution failed";
    return NextResponse.json(
      { error: message },
      { status: 400 },
    );
  }
}
