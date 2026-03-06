/**
 * POST /api/chat — Streaming agentic chat via Claude Agent SDK.
 * Uses V1 query() with custom MCP tools for querying trajectory data,
 * reading logs, running Python analysis. Streams NDJSON events back.
 */

import { NextRequest } from "next/server";
import { query, createSdkMcpServer, tool } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { getProjectFromRequest } from "@/lib/server/project-context";
import { getSchemaInfo } from "@/lib/server/data";
import { buildSystemPrompt } from "@/lib/chat/system-prompt";
import { executeTool } from "@/lib/chat/tools";
import type { ChatRequest, ChatStreamEvent } from "@/lib/chat/types";

export const maxDuration = 120;

/** Send an NDJSON line to the stream */
function sendEvent(
  controller: ReadableStreamDefaultController,
  event: ChatStreamEvent,
) {
  controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + "\n"));
}

/** Build MCP tools that delegate to our server-side handlers */
function buildMcpServer(project: string) {
  return createSdkMcpServer({
    name: "envoi-tools",
    version: "1.0.0",
    tools: [
      tool(
        "query_sql",
        "Execute a read-only SQL query against the project DuckDB database. Supports SELECT, SHOW, DESCRIBE, PRAGMA. Results limited to 1000 rows.",
        { sql: z.string().describe("The SQL query to execute") },
        async (args) => {
          const result = await executeTool("query_sql", { sql: args.sql }, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
      tool(
        "describe_tables",
        "Get the schema of all tables in the database — table names, column names, and data types.",
        {},
        async () => {
          const result = await executeTool("describe_tables", {}, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
      tool(
        "list_trajectories",
        "List all trajectories in the current project with summary info.",
        {
          environment: z.string().optional().describe("Filter by environment name"),
          model: z.string().optional().describe("Filter by agent model"),
          limit: z.number().optional().describe("Max trajectories (default 50)"),
        },
        async (args) => {
          const result = await executeTool("list_trajectories", args, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
      tool(
        "get_trajectory",
        "Get full details of a trajectory by ID — commits, evaluations, suites, timing.",
        { trajectoryId: z.string().describe("The trajectory ID") },
        async (args) => {
          const result = await executeTool("get_trajectory", args, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
      tool(
        "get_trajectory_logs",
        "Get structured logs for a trajectory — component, event, level, message, timestamps.",
        {
          trajectoryId: z.string().describe("The trajectory ID"),
          limit: z.number().optional().describe("Max log rows (default 200)"),
        },
        async (args) => {
          const result = await executeTool("get_trajectory_logs", args, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
      tool(
        "get_code_snapshot",
        "Get the source code files at a specific commit in a trajectory.",
        {
          trajectoryId: z.string().describe("The trajectory ID"),
          commitIndex: z.number().describe("The commit index (0-based)"),
        },
        async (args) => {
          const result = await executeTool("get_code_snapshot", args, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
      tool(
        "read_file",
        "Read a file from the web app source code (read-only). Path relative to packages/web/src/.",
        { path: z.string().describe("Path relative to packages/web/src/") },
        async (args) => {
          const result = await executeTool("read_file", args, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
      tool(
        "run_python",
        "Execute Python in a sandboxed temp directory. Has access to duckdb, matplotlib, json, csv. For charts: save as SVG. DuckDB path available as DB_PATH env var.",
        { code: z.string().describe("Python code to execute") },
        async (args) => {
          const result = await executeTool("run_python", { code: args.code }, project);
          return { content: [{ type: "text" as const, text: result.output }] };
        },
      ),
    ],
  });
}

export async function POST(request: NextRequest) {
  const project = await getProjectFromRequest(request);
  if (!project) {
    return new Response("Project not selected", { status: 400 });
  }

  let body: ChatRequest;
  try {
    body = await request.json();
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }

  const { messages, pageContext } = body;

  const schema = await getSchemaInfo(project);
  const systemPrompt = buildSystemPrompt(pageContext, schema);

  /** Build the full prompt from conversation history */
  const conversationParts = messages
    .filter((msg) => msg.blocks.length > 0)
    .map((msg) => {
      const role = msg.role === "user" ? "User" : "Assistant";
      const text = msg.blocks
        .filter((block) => block.type === "text")
        .map((block) => (block as { type: "text"; text: string }).text)
        .join("\n");
      return `${role}: ${text}`;
    });

  const fullPrompt = conversationParts.join("\n\n");
  const mcpServer = buildMcpServer(project);

  const abortController = new AbortController();
  request.signal.addEventListener("abort", () => abortController.abort());

  const stream = new ReadableStream({
    async start(controller) {
      try {
        for await (const message of query({
          prompt: fullPrompt,
          options: {
            abortController,
            systemPrompt,
            model: "claude-sonnet-4-5-20250514",
            maxTurns: 15,
            tools: [],
            mcpServers: { "envoi-tools": mcpServer },
            allowedTools: [
              "mcp__envoi-tools__query_sql",
              "mcp__envoi-tools__describe_tables",
              "mcp__envoi-tools__list_trajectories",
              "mcp__envoi-tools__get_trajectory",
              "mcp__envoi-tools__get_trajectory_logs",
              "mcp__envoi-tools__get_code_snapshot",
              "mcp__envoi-tools__read_file",
              "mcp__envoi-tools__run_python",
            ],
            permissionMode: "bypassPermissions",
            allowDangerouslySkipPermissions: true,
            includePartialMessages: true,
          },
        })) {
          if (request.signal.aborted) {
            break;
          }

          if (message.type === "stream_event") {
            const event = message.event;
            if (event.type === "content_block_delta" && "delta" in event) {
              const delta = event.delta;
              if (
                typeof delta === "object" &&
                delta !== null &&
                "type" in delta &&
                delta.type === "text_delta" &&
                "text" in delta &&
                typeof delta.text === "string"
              ) {
                sendEvent(controller, {
                  type: "text-delta",
                  text: delta.text,
                });
              }
            }
          } else if (message.type === "assistant") {
            for (const block of message.message.content) {
              if (block.type === "tool_use") {
                const toolInput =
                  typeof block.input === "object" && block.input !== null
                    ? (block.input as Record<string, unknown>)
                    : {};
                sendEvent(controller, {
                  type: "tool-call-start",
                  toolName: block.name,
                  input: toolInput,
                  callId: block.id,
                });
              }
            }
          }
        }

        sendEvent(controller, { type: "done" });
      } catch (error) {
        const errorName = error instanceof Error ? error.name : "";
        if (errorName === "AbortError" || request.signal.aborted) {
          sendEvent(controller, { type: "done" });
        } else {
          sendEvent(controller, {
            type: "error",
            message: error instanceof Error ? error.message : "Unknown error",
          });
        }
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
