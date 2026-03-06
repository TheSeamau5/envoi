/**
 * Agent tool definitions and server-side handlers.
 * Each tool has a name, description, input schema (for the agent),
 * and a handler function that executes on the server.
 */

import {
  executeQuery,
  getSchemaInfo,
  getAllTrajectories,
  getTrajectoryById,
  getTrajectoryLogsById,
  getCodeHistory,
} from "@/lib/server/data";
import { executePython } from "./python-sandbox";
import { readFile } from "fs/promises";
import { resolve, normalize } from "path";

/** Tool definition for the agent */
export type ToolDefinition = {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
};

/** Result of executing a tool */
export type ToolResult = {
  output: string;
  images?: Array<{ src: string; alt: string }>;
  table?: { columns: string[]; rows: unknown[][] };
};

/** All tool definitions exposed to the agent */
export const TOOL_DEFINITIONS: ToolDefinition[] = [
  {
    name: "query_sql",
    description:
      "Execute a read-only SQL query against the project's DuckDB database. " +
      "Supports SELECT, SHOW, DESCRIBE, PRAGMA. Mutating queries are rejected. " +
      "Results limited to 1000 rows. Use this for data exploration and analysis.",
    inputSchema: {
      type: "object",
      properties: {
        sql: {
          type: "string",
          description: "The SQL query to execute",
        },
      },
      required: ["sql"],
    },
  },
  {
    name: "describe_tables",
    description:
      "Get the schema of all tables in the database — table names, column names, and data types.",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "list_trajectories",
    description:
      "List all trajectories in the current project with summary info (id, model, environment, parts, tokens, test results).",
    inputSchema: {
      type: "object",
      properties: {
        environment: {
          type: "string",
          description: "Optional: filter by environment name",
        },
        model: {
          type: "string",
          description: "Optional: filter by agent model name",
        },
        limit: {
          type: "number",
          description: "Max trajectories to return (default 50)",
        },
      },
    },
  },
  {
    name: "get_trajectory",
    description:
      "Get full details of a trajectory by ID — includes commits, steps, evaluations, suites, timing.",
    inputSchema: {
      type: "object",
      properties: {
        trajectoryId: {
          type: "string",
          description: "The trajectory ID",
        },
      },
      required: ["trajectoryId"],
    },
  },
  {
    name: "get_trajectory_logs",
    description:
      "Get structured logs for a trajectory — includes component, event, level, message, timestamps.",
    inputSchema: {
      type: "object",
      properties: {
        trajectoryId: {
          type: "string",
          description: "The trajectory ID",
        },
        limit: {
          type: "number",
          description: "Max log rows to return (default 200)",
        },
      },
      required: ["trajectoryId"],
    },
  },
  {
    name: "get_code_snapshot",
    description:
      "Get the source code files at a specific commit in a trajectory. " +
      "Returns file paths and contents for the entire codebase at that point.",
    inputSchema: {
      type: "object",
      properties: {
        trajectoryId: {
          type: "string",
          description: "The trajectory ID",
        },
        commitIndex: {
          type: "number",
          description: "The commit index (0-based)",
        },
      },
      required: ["trajectoryId"],
    },
  },
  {
    name: "read_file",
    description:
      "Read a file from the web app source code (read-only). " +
      "Path is relative to the packages/web/src/ directory. " +
      "Use this to understand how the dashboard works.",
    inputSchema: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description: "Path relative to packages/web/src/, e.g. 'lib/types.ts'",
        },
      },
      required: ["path"],
    },
  },
  {
    name: "run_python",
    description:
      "Execute a Python script in a sandboxed environment. " +
      "Python has access to: duckdb, json, csv, os, math, datetime, sys. " +
      "For charts: use matplotlib, save as SVG (e.g. plt.savefig('chart.svg')). " +
      "Print results to stdout as text or JSON. " +
      "The DuckDB database path is available as the DB_PATH environment variable. " +
      "Open it in read-only mode: duckdb.connect(os.environ['DB_PATH'], read_only=True)",
    inputSchema: {
      type: "object",
      properties: {
        code: {
          type: "string",
          description: "The Python code to execute",
        },
      },
      required: ["code"],
    },
  },
];

/** Allowed base directory for read_file */
const WEB_SRC_DIR = resolve(process.cwd(), "src");

/** Execute a tool by name and return the result */
export async function executeTool(
  toolName: string,
  input: Record<string, unknown>,
  project: string,
): Promise<ToolResult> {
  switch (toolName) {
    case "query_sql":
      return handleQuerySql(String(input.sql ?? ""), project);
    case "describe_tables":
      return handleDescribeTables(project);
    case "list_trajectories":
      return handleListTrajectories(input, project);
    case "get_trajectory":
      return handleGetTrajectory(String(input.trajectoryId ?? ""), project);
    case "get_trajectory_logs":
      return handleGetTrajectoryLogs(
        String(input.trajectoryId ?? ""),
        Number(input.limit ?? 200),
        project,
      );
    case "get_code_snapshot":
      return handleGetCodeSnapshot(
        String(input.trajectoryId ?? ""),
        Number(input.commitIndex ?? 0),
        project,
      );
    case "read_file":
      return handleReadFile(String(input.path ?? ""));
    case "run_python":
      return handleRunPython(String(input.code ?? ""), project);
    default:
      return { output: `Unknown tool: ${toolName}` };
  }
}

// ---------------------------------------------------------------------------
// Tool handlers
// ---------------------------------------------------------------------------

const MUTATING_KEYWORDS =
  /\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|TRUNCATE|REPLACE)\b/i;

async function handleQuerySql(sql: string, project: string): Promise<ToolResult> {
  const trimmed = sql.trim();
  if (!trimmed) {
    return { output: "Error: SQL query cannot be empty" };
  }
  if (MUTATING_KEYWORDS.test(trimmed)) {
    return { output: "Error: Only read-only queries are allowed" };
  }

  const hasLimit = /\bLIMIT\b/i.test(trimmed);
  const safeSql = hasLimit ? trimmed : `${trimmed} LIMIT 1000`;

  try {
    const result = await executeQuery(safeSql, project);
    if (result.rows.length === 0) {
      return { output: "Query returned 0 rows." };
    }
    return {
      output: `${result.rowCount} rows returned (${result.durationMs}ms)`,
      table: { columns: result.columns, rows: result.rows.map((row) => result.columns.map((col) => row[col])) },
    };
  } catch (error) {
    return { output: `SQL Error: ${error instanceof Error ? error.message : String(error)}` };
  }
}

async function handleDescribeTables(project: string): Promise<ToolResult> {
  try {
    const schema = await getSchemaInfo(project);
    if (schema.length === 0) {
      return { output: "No tables found or database not configured." };
    }
    const columns = ["table_name", "column_name", "data_type"];
    const rows = schema.map((col) => [col.tableName, col.columnName, col.dataType]);
    return { output: `${schema.length} columns across tables`, table: { columns, rows } };
  } catch (error) {
    return { output: `Error: ${error instanceof Error ? error.message : String(error)}` };
  }
}

async function handleListTrajectories(
  input: Record<string, unknown>,
  project: string,
): Promise<ToolResult> {
  try {
    const trajectories = await getAllTrajectories({
      project,
      environment: input.environment ? String(input.environment) : undefined,
      model: input.model ? String(input.model) : undefined,
      limit: input.limit ? Number(input.limit) : 50,
    });

    if (trajectories.length === 0) {
      return { output: "No trajectories found." };
    }

    const columns = ["id", "model", "environment", "tokens", "passed", "total_tests", "ended"];
    const rows = trajectories.map((traj) => [
      traj.id.slice(0, 8),
      traj.model,
      traj.environment,
      traj.totalTokens,
      traj.finalPassed,
      traj.totalTests,
      traj.sessionEndReason ?? "running",
    ]);

    return {
      output: `${trajectories.length} trajectories`,
      table: { columns, rows },
    };
  } catch (error) {
    return { output: `Error: ${error instanceof Error ? error.message : String(error)}` };
  }
}

async function handleGetTrajectory(trajectoryId: string, project: string): Promise<ToolResult> {
  try {
    const trajectory = await getTrajectoryById(trajectoryId, { project });
    if (!trajectory) {
      return { output: `Trajectory ${trajectoryId} not found.` };
    }
    const summary = {
      id: trajectory.id,
      model: trajectory.model,
      environment: trajectory.environment,
      agentHarness: trajectory.agentHarness,
      totalTokens: trajectory.totalTokens,
      totalTests: trajectory.totalTests,
      finalPassed: trajectory.finalPassed,
      sessionEndReason: trajectory.sessionEndReason,
      startedAt: trajectory.startedAt,
      duration: trajectory.duration,
      suites: trajectory.suites,
      commitCount: trajectory.commits.length,
      cost: trajectory.cost,
    };
    return { output: JSON.stringify(summary, undefined, 2) };
  } catch (error) {
    return { output: `Error: ${error instanceof Error ? error.message : String(error)}` };
  }
}

async function handleGetTrajectoryLogs(
  trajectoryId: string,
  limit: number,
  project: string,
): Promise<ToolResult> {
  try {
    const logs = await getTrajectoryLogsById(trajectoryId, {
      project,
      limit,
    });
    if (!logs || logs.length === 0) {
      return { output: "No logs found for this trajectory." };
    }
    const columns = ["seq", "timestamp", "component", "event", "level", "message"];
    const rows = logs.map((log) => [
      log.seq,
      log.ts,
      log.component,
      log.event,
      log.level,
      log.message?.slice(0, 200),
    ]);
    return {
      output: `${logs.length} log entries`,
      table: { columns, rows },
    };
  } catch (error) {
    return { output: `Error: ${error instanceof Error ? error.message : String(error)}` };
  }
}

async function handleGetCodeSnapshot(
  trajectoryId: string,
  commitIndex: number,
  project: string,
): Promise<ToolResult> {
  try {
    const history = await getCodeHistory(trajectoryId, project);
    if (!history) {
      return { output: "No code history found for this trajectory." };
    }
    const snapshot = history[commitIndex];
    if (!snapshot) {
      return { output: `Commit index ${commitIndex} not found. Available: 0-${Object.keys(history).length - 1}` };
    }
    const files = Object.entries(snapshot).map(([path, fileSnap]) => ({
      path,
      lineCount: fileSnap.lines.length,
      touched: fileSnap.touched,
    }));
    return {
      output: `${files.length} files at commit ${commitIndex}:\n` +
        files.map((file) => `  ${file.path} (${file.lineCount} lines${file.touched ? ", modified" : ""})`).join("\n"),
    };
  } catch (error) {
    return { output: `Error: ${error instanceof Error ? error.message : String(error)}` };
  }
}

async function handleReadFile(relativePath: string): Promise<ToolResult> {
  try {
    const normalized = normalize(relativePath);
    if (normalized.includes("..")) {
      return { output: "Error: Path traversal not allowed" };
    }
    const fullPath = resolve(WEB_SRC_DIR, normalized);
    if (!fullPath.startsWith(WEB_SRC_DIR)) {
      return { output: "Error: Path must be within packages/web/src/" };
    }
    const content = await readFile(fullPath, "utf-8");
    return { output: content };
  } catch (error) {
    return {
      output: `Error reading file: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

async function handleRunPython(code: string, project: string): Promise<ToolResult> {
  try {
    return await executePython(code, project);
  } catch (error) {
    return { output: `Python error: ${error instanceof Error ? error.message : String(error)}` };
  }
}
