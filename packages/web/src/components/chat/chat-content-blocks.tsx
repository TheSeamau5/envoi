/**
 * Renderers for each content block type within a chat message.
 * Handles text/markdown, tables, images, tool calls, and code blocks.
 */

"use client";

import { useState } from "react";
import Image from "next/image";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Copy,
  Check,
} from "lucide-react";
import type { ContentBlock, TableBlock, ToolCallBlock } from "@/lib/chat/types";
import { ChatMarkdown } from "./chat-markdown";

type ContentBlocksProps = {
  blocks: ContentBlock[];
};

/** Render a list of content blocks */
export function ChatContentBlocks({ blocks }: ContentBlocksProps) {
  if (blocks.length === 0) {
    return (
      <span className="text-envoi-text-dim">
        <Loader2 size={14} className="animate-spin" />
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {blocks.map((block, index) => (
        <ContentBlockRenderer key={index} block={block} />
      ))}
    </div>
  );
}

/** Dispatch to the right renderer based on block type */
function ContentBlockRenderer({ block }: { block: ContentBlock }) {
  switch (block.type) {
    case "text":
      return <ChatMarkdown text={block.text} />;
    case "table":
      return <TableRenderer table={block} />;
    case "image":
      return (
        <Image
          src={block.src}
          alt={block.alt ?? "Chart"}
          width={1200}
          height={800}
          unoptimized
          className="h-auto max-w-full rounded border border-envoi-border"
        />
      );
    case "tool-call":
      return <ToolCallRenderer toolCall={block} />;
    case "code":
      return <CodeBlockRenderer code={block.code} language={block.language} filename={block.filename} />;
  }
}

/** Render a structured table with headers and rows */
function TableRenderer({ table }: { table: TableBlock }) {
  return (
    <div className="overflow-x-auto rounded border border-envoi-border">
      {table.caption && (
        <div className="border-b border-envoi-border px-2 py-1 text-[11px] font-bold uppercase tracking-widest text-envoi-text-muted">
          {table.caption}
        </div>
      )}
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-envoi-border bg-envoi-surface">
            {table.columns.map((col) => (
              <th
                key={col}
                className="px-2 py-1 text-left font-semibold text-envoi-text-muted"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              className="border-b border-envoi-border last:border-b-0"
            >
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-2 py-1 text-envoi-text">
                  {String(cell ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Render a collapsible tool call with status indicator */
function ToolCallRenderer({ toolCall }: { toolCall: ToolCallBlock }) {
  const [expanded, setExpanded] = useState(false);

  const statusIcon = (() => {
    switch (toolCall.status) {
      case "pending":
      case "running":
        return <Loader2 size={12} className="animate-spin text-envoi-accent" />;
      case "done":
        return <CheckCircle2 size={12} className="text-envoi-green" />;
      case "error":
        return <AlertCircle size={12} className="text-envoi-red" />;
    }
  })();

  return (
    <div className="rounded border border-envoi-border">
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-[12px] text-envoi-text-muted transition-colors hover:bg-envoi-surface"
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {statusIcon}
        <span className="font-semibold">{toolCall.toolName}</span>
      </button>
      {expanded && (
        <div className="border-t border-envoi-border px-2 py-1.5">
          <div className="text-[11px] font-bold uppercase tracking-widest text-envoi-text-dim">
            Input
          </div>
          <pre className="mt-1 overflow-x-auto text-[11px] text-envoi-text-muted">
            {JSON.stringify(toolCall.input, undefined, 2)}
          </pre>
          {toolCall.output && (
            <>
              <div className="mt-2 text-[11px] font-bold uppercase tracking-widest text-envoi-text-dim">
                Output
              </div>
              <pre className="mt-1 max-h-[200px] overflow-auto text-[11px] text-envoi-text-muted">
                {toolCall.output}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Render a syntax-highlighted code block with copy button */
function CodeBlockRenderer({
  code,
  language,
  filename,
}: {
  code: string;
  language: string;
  filename?: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="overflow-hidden rounded border border-envoi-border">
      <div className="flex items-center justify-between border-b border-envoi-border bg-envoi-surface px-2 py-1">
        <span className="text-[11px] text-envoi-text-muted">
          {filename ?? language}
        </span>
        <button
          onClick={handleCopy}
          className="rounded p-0.5 text-envoi-text-dim transition-colors hover:text-envoi-text-muted"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>
      <pre className="overflow-x-auto px-2 py-1.5 text-[12px] text-envoi-text">
        <code>{code}</code>
      </pre>
    </div>
  );
}
