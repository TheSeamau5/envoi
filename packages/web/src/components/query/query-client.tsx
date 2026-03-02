/**
 * SQL Console client component.
 * Textarea editor, results table, and schema reference sidebar.
 */

"use client";

import { useState, useCallback, useRef } from "react";
import { Play, Loader2, ChevronRight, ChevronDown } from "lucide-react";
import type { SchemaColumn } from "@/lib/types";
import { T } from "@/lib/tokens";

type QueryClientProps = {
  schema: SchemaColumn[];
};

type QueryResult = {
  rows: Record<string, unknown>[];
  columns: string[];
  rowCount: number;
  durationMs: number;
};

const EXAMPLE_QUERIES = [
  "SELECT * FROM trajectories LIMIT 10",
  "SELECT agent_model, COUNT(*) AS count FROM trajectories GROUP BY agent_model",
  "SELECT * FROM evaluations LIMIT 10",
  "SELECT * FROM turn_summaries LIMIT 10",
  "SELECT * FROM file_access LIMIT 10",
];

export function QueryClient({ schema }: QueryClientProps) {
  const [sql, setSql] = useState("");
  const [result, setResult] = useState<QueryResult>();
  const [error, setError] = useState<string>();
  const [isLoading, setIsLoading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const runQuery = useCallback(async (queryText: string) => {
    if (queryText.trim().length === 0) {
      return;
    }

    setIsLoading(true);
    setError(undefined);
    setResult(undefined);

    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql: queryText }),
      });
      const data: unknown = await response.json();

      if (!response.ok) {
        const errorData = data as Record<string, unknown>;
        setError(String(errorData.error ?? "Query failed"));
        return;
      }

      setResult(data as QueryResult);
    } catch {
      setError("Failed to execute query");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      runQuery(sql);
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      const target = event.currentTarget;
      const start = target.selectionStart;
      const end = target.selectionEnd;
      const newValue = sql.slice(0, start) + "  " + sql.slice(end);
      setSql(newValue);
      requestAnimationFrame(() => {
        target.selectionStart = start + 2;
        target.selectionEnd = start + 2;
      });
    }
  }, [sql, runQuery]);

  /** Group schema columns by table */
  const tables = new Map<string, SchemaColumn[]>();
  for (const col of schema) {
    const existing = tables.get(col.tableName);
    if (existing) {
      existing.push(col);
    } else {
      tables.set(col.tableName, [col]);
    }
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Main area: editor + results */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* SQL Editor */}
        <div className="flex flex-col gap-[8px] border-b border-envoi-border px-[14px] py-[10px]">
          <textarea
            ref={textareaRef}
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter SQL query... (Cmd+Enter to run)"
            className="min-h-[120px] w-full resize-y rounded border border-envoi-border-light bg-envoi-surface px-[10px] py-[8px] text-[11px] leading-[18px] text-envoi-text outline-none focus:border-envoi-accent"
            style={{ fontFamily: T.mono }}
          />
          <div className="flex items-center gap-[8px]">
            <button
              onClick={() => runQuery(sql)}
              disabled={isLoading || sql.trim().length === 0}
              className="flex items-center gap-[6px] rounded bg-envoi-accent px-[12px] py-[4px] text-[10px] font-semibold text-white transition-opacity disabled:opacity-40"
            >
              {isLoading ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Play size={12} />
              )}
              Run Query
            </button>
            <span className="text-[9px] text-envoi-text-dim">
              Cmd+Enter
            </span>
          </div>

          {/* Example queries */}
          <div className="flex flex-wrap items-center gap-[4px]">
            <span className="text-[9px] text-envoi-text-dim">Examples:</span>
            {EXAMPLE_QUERIES.map((example) => (
              <button
                key={example}
                onClick={() => {
                  setSql(example);
                  textareaRef.current?.focus();
                }}
                className="rounded bg-envoi-surface px-[6px] py-[2px] text-[9px] text-envoi-text-muted transition-colors hover:bg-envoi-border-light hover:text-envoi-text"
              >
                {example.length > 50 ? `${example.slice(0, 50)}...` : example}
              </button>
            ))}
          </div>
        </div>

        {/* Results area */}
        <div className="flex-1 overflow-auto">
          {error && (
            <div
              className="border-b px-[14px] py-[10px] text-[11px]"
              style={{
                background: T.redBgOpaque,
                color: T.redDark,
                borderColor: T.redBorderLight,
              }}
            >
              {error}
            </div>
          )}

          {result && (
            <div className="flex flex-col">
              {/* Stats bar */}
              <div className="flex items-center gap-[10px] border-b border-envoi-border-light px-[14px] py-[4px]">
                <span className="text-[9px] text-envoi-text-dim">
                  {result.rowCount} rows
                </span>
                <span className="text-[9px] text-envoi-text-dim">
                  {result.durationMs}ms
                </span>
              </div>

              {/* Table */}
              {result.columns.length > 0 && (
                <div className="overflow-auto">
                  <table className="w-full border-collapse text-[10px]">
                    <thead>
                      <tr>
                        {result.columns.map((col) => (
                          <th
                            key={col}
                            className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[10px] py-[4px] text-left font-semibold text-envoi-text-muted"
                          >
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.rows.map((row, rowIndex) => (
                        <tr
                          key={rowIndex}
                          className={rowIndex % 2 === 0 ? "bg-envoi-bg" : "bg-envoi-surface"}
                        >
                          {result.columns.map((col) => (
                            <td
                              key={col}
                              className="border-b border-envoi-border-light px-[10px] py-[3px] text-envoi-text"
                              style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                            >
                              {formatValue(row[col])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {!result && !error && !isLoading && (
            <div className="flex flex-1 items-center justify-center py-[40px] text-[11px] text-envoi-text-dim">
              Run a query to see results
            </div>
          )}
        </div>
      </div>

      {/* Schema sidebar */}
      <div className="flex w-[240px] shrink-0 flex-col overflow-y-auto border-l border-envoi-border bg-envoi-surface">
        <div className="border-b border-envoi-border px-[12px] py-[6px]">
          <span className="text-[9px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Schema
          </span>
        </div>
        {Array.from(tables.entries()).map(([tableName, columns]) => (
          <SchemaTable key={tableName} tableName={tableName} columns={columns} />
        ))}
        {tables.size === 0 && (
          <div className="px-[12px] py-[10px] text-[10px] text-envoi-text-dim">
            No tables available (S3 not configured)
          </div>
        )}
      </div>
    </div>
  );
}

/** Collapsible schema table entry */
function SchemaTable({ tableName, columns }: { tableName: string; columns: SchemaColumn[] }) {
  const [expanded, setExpanded] = useState(false);
  const ChevronIcon = expanded ? ChevronDown : ChevronRight;

  return (
    <div className="border-b border-envoi-border-light">
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-center gap-[6px] px-[12px] py-[6px] text-left text-[10px] font-semibold text-envoi-text hover:bg-envoi-border-light"
      >
        <ChevronIcon size={10} className="text-envoi-text-dim" />
        {tableName}
        <span className="text-envoi-text-dim">({columns.length})</span>
      </button>
      {expanded && (
        <div className="pb-[4px]">
          {columns.map((col) => (
            <div
              key={col.columnName}
              className="flex items-center gap-[6px] px-[24px] py-[2px] text-[9px]"
            >
              <span className="text-envoi-text">{col.columnName}</span>
              <span className="text-envoi-text-dim">{col.dataType}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Format a cell value for display */
function formatValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "NULL";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
