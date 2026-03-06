/**
 * Markdown renderer for chat messages.
 * Uses react-markdown with remark-gfm for tables, strikethrough, etc.
 * Code blocks get syntax highlighting via inline styles (no Shiki in client).
 */

"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

type ChatMarkdownProps = {
  text: string;
};

/** Custom component overrides for react-markdown */
const components: Components = {
  p: ({ children }) => (
    <p className="text-[13px] leading-relaxed">{children}</p>
  ),
  strong: ({ children }) => (
    <strong className="font-bold">{children}</strong>
  ),
  em: ({ children }) => <em>{children}</em>,
  h1: ({ children }) => (
    <h1 className="text-[15px] font-bold">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-[14px] font-bold">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-[13px] font-bold">{children}</h3>
  ),
  ul: ({ children }) => (
    <ul className="list-disc pl-4 text-[13px]">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal pl-4 text-[13px]">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="leading-relaxed">{children}</li>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-envoi-accent underline hover:text-envoi-accent-dark"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-envoi-border pl-3 text-envoi-text-muted">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    const isInline = !className;
    if (isInline) {
      return (
        <code className="rounded bg-envoi-surface px-1 py-0.5 text-[12px]">
          {children}
        </code>
      );
    }
    return (
      <pre className="overflow-x-auto rounded border border-envoi-border bg-envoi-surface px-2 py-1.5 text-[12px]">
        <code>{children}</code>
      </pre>
    );
  },
  table: ({ children }) => (
    <div className="overflow-x-auto rounded border border-envoi-border">
      <table className="w-full text-[12px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="border-b border-envoi-border bg-envoi-surface">
      {children}
    </thead>
  ),
  tr: ({ children }) => (
    <tr className="border-b border-envoi-border last:border-b-0">
      {children}
    </tr>
  ),
  th: ({ children }) => (
    <th className="px-2 py-1 text-left font-semibold text-envoi-text-muted">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-2 py-1">{children}</td>
  ),
};

/** Render markdown text with GFM support */
export function ChatMarkdown({ text }: ChatMarkdownProps) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {text}
    </ReactMarkdown>
  );
}
