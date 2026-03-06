/**
 * Build the system prompt for the chat agent.
 * Includes role description, available tools, database schema,
 * and current page context.
 */

import type { ChatPageContext } from "./types";
import type { SchemaColumn } from "@/lib/types";

/** Format schema columns into a readable string */
function formatSchema(columns: SchemaColumn[]): string {
  const byTable = new Map<string, SchemaColumn[]>();
  for (const col of columns) {
    const existing = byTable.get(col.tableName);
    if (existing) {
      existing.push(col);
    } else {
      byTable.set(col.tableName, [col]);
    }
  }

  const parts: string[] = [];
  for (const [tableName, cols] of byTable) {
    const colLines = cols
      .map((col) => `  ${col.columnName} (${col.dataType})`)
      .join("\n");
    parts.push(`TABLE ${tableName}:\n${colLines}`);
  }
  return parts.join("\n\n");
}

/** Format page context into a description */
function formatPageContext(context: ChatPageContext): string {
  switch (context.page) {
    case "trajectory":
      return `The user is currently viewing trajectory detail page for trajectory ID: ${context.trajectoryId ?? "unknown"}` +
        (context.project ? ` in project "${context.project}"` : "");
    case "compare":
      return `The user is on the compare page` +
        (context.selectedIds?.length
          ? ` comparing trajectories: ${context.selectedIds.join(", ")}`
          : "") +
        (context.project ? ` in project "${context.project}"` : "");
    case "difficulty":
      return `The user is viewing the difficulty heatmap` +
        (context.project ? ` for project "${context.project}"` : "");
    case "portfolio":
      return `The user is viewing the portfolio/performance page` +
        (context.project ? ` for project "${context.project}"` : "");
    default:
      return context.project
        ? `The user is browsing project "${context.project}"`
        : "The user is browsing the dashboard";
  }
}

/** Build the full system prompt for the agent */
export function buildSystemPrompt(
  pageContext: ChatPageContext,
  schema: SchemaColumn[],
): string {
  const schemaText = schema.length > 0
    ? formatSchema(schema)
    : "Schema not available — use the describe_tables tool to discover table structure.";

  const contextText = formatPageContext(pageContext);

  return `You are an analyst assistant for the Envoi dashboard — a platform for evaluating AI coding agents.

## Your Role
Help users understand trajectory data: agent performance, test results, timing, code changes, logs, and comparisons across runs. Be concise and data-driven. Format results as markdown tables when showing tabular data.

## Current Context
${contextText}

## Available Database Schema (DuckDB)
${schemaText}

## Key Concepts
- **Trajectory**: One complete agent run (attempt to solve a coding problem)
- **Part**: Smallest unit of agent action (reasoning, tool use, code edit, etc.)
- **Turn**: One request/response cycle (contains many parts)
- **Suite**: Group of related tests (e.g., "basics", "wacct", "c_testsuite", "torture")
- **Commit**: Git checkpoint created when files change during a run

## Key Tables
- **trajectories**: One row per trajectory — has trajectory_id, agent_model, environment, total_parts, total_turns, total_tokens, session_end_reason, started_at, suites (JSON)
- **evaluations**: Test results per evaluation point — trajectory_id, suite, test_path, passed, commit_index

## Important Notes
- The eval_events_delta column is extremely large — NEVER SELECT it. Use the evaluations table instead.
- Always use LIMIT in queries unless you know the result set is small.
- For test pass rates, query the evaluations table grouped by suite.
- Use run_python for complex analysis, visualizations, or when you need to process data programmatically.
- When generating charts with Python, save as SVG to the working directory and they will be displayed inline.
- DuckDB database path will be provided when using run_python.`;
}
