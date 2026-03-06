/**
 * Type definitions for the agentic chat system.
 * Messages contain content blocks (text, tables, images, tool calls)
 * rather than plain strings — enabling rich rendering.
 */

/** A single content block within a message */
export type ContentBlock =
  | TextBlock
  | TableBlock
  | ImageBlock
  | ToolCallBlock
  | CodeBlock;

/** Markdown text content */
export type TextBlock = {
  type: "text";
  text: string;
};

/** Structured table from query results or Python output */
export type TableBlock = {
  type: "table";
  columns: string[];
  rows: unknown[][];
  caption?: string;
};

/** Image (chart, screenshot) — stored as base64 data URL or blob URL */
export type ImageBlock = {
  type: "image";
  src: string;
  alt?: string;
  width?: number;
  height?: number;
};

/** A tool invocation with its input and result */
export type ToolCallBlock = {
  type: "tool-call";
  toolName: string;
  input: Record<string, unknown>;
  output?: string;
  status: "pending" | "running" | "done" | "error";
};

/** Syntax-highlighted code block */
export type CodeBlock = {
  type: "code";
  code: string;
  language: string;
  filename?: string;
};

/** A single chat message */
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  blocks: ContentBlock[];
  timestamp: number;
};

/** Context about what page the user is currently viewing */
export type ChatPageContext = {
  page: string;
  project?: string;
  trajectoryId?: string;
  selectedIds?: string[];
};

/** Streaming event from the /api/chat endpoint (NDJSON) */
export type ChatStreamEvent =
  | { type: "text-delta"; text: string }
  | { type: "tool-call-start"; toolName: string; input: Record<string, unknown>; callId: string }
  | { type: "tool-call-done"; callId: string; output: string }
  | { type: "tool-call-error"; callId: string; error: string }
  | { type: "table"; columns: string[]; rows: unknown[][]; caption?: string }
  | { type: "image"; src: string; alt?: string }
  | { type: "code"; code: string; language: string; filename?: string }
  | { type: "done" }
  | { type: "error"; message: string };

/** Request body sent to /api/chat */
export type ChatRequest = {
  messages: ChatMessage[];
  pageContext: ChatPageContext;
};
