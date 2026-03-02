/**
 * Unified SQL Console + Templates client component.
 * Templates sidebar (left), SQL editor + results (center), schema sidebar (right).
 * Templates can be built-in or user-saved (localStorage).
 */

"use client";

import { useState, useCallback, useRef, useMemo } from "react";
import {
  Play,
  Loader2,
  ChevronRight,
  ChevronDown,
  Plus,
  Trash2,
  BookmarkPlus,
} from "lucide-react";
import type { SchemaColumn, QueryTemplate } from "@/lib/types";
import { T } from "@/lib/tokens";
import { usePersistedState } from "@/lib/storage";

type QueryClientProps = {
  schema: SchemaColumn[];
  builtinTemplates: QueryTemplate[];
};

type QueryResult = {
  rows: Record<string, unknown>[];
  columns: string[];
  rowCount: number;
  durationMs: number;
};

/** Empty array default for usePersistedState */
const EMPTY_TEMPLATES: QueryTemplate[] = [];

/** SVG bar chart layout constants */
const BAR_VIEW_W = 600;
const BAR_VIEW_H = 200;
const BAR_MARGIN = { top: 20, right: 20, bottom: 40, left: 60 };
const BAR_PLOT_W = BAR_VIEW_W - BAR_MARGIN.left - BAR_MARGIN.right;
const BAR_PLOT_H = BAR_VIEW_H - BAR_MARGIN.top - BAR_MARGIN.bottom;

/** SVG line chart layout constants */
const LINE_VIEW_W = 600;
const LINE_VIEW_H = 200;
const LINE_MARGIN = { top: 20, right: 20, bottom: 40, left: 60 };
const LINE_PLOT_W = LINE_VIEW_W - LINE_MARGIN.left - LINE_MARGIN.right;
const LINE_PLOT_H = LINE_VIEW_H - LINE_MARGIN.top - LINE_MARGIN.bottom;

export function QueryClient({ schema, builtinTemplates }: QueryClientProps) {
  const [sql, setSql] = useState("");
  const [result, setResult] = useState<QueryResult>();
  const [error, setError] = useState<string>();
  const [isLoading, setIsLoading] = useState(false);
  const [savedTemplates, setSavedTemplates] = usePersistedState<QueryTemplate[]>("saved-templates", EMPTY_TEMPLATES);
  const [activeTemplateId, setActiveTemplateId] = useState<string>();
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [showSaveForm, setShowSaveForm] = useState(false);
  const [saveTemplateName, setSaveTemplateName] = useState("");
  const [activeVisualization, setActiveVisualization] = useState<"table" | "bar" | "line">("table");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const allTemplates = useMemo(
    () => [...builtinTemplates, ...savedTemplates],
    [builtinTemplates, savedTemplates],
  );

  const activeTemplate = useMemo(
    () => allTemplates.find((tmpl) => tmpl.id === activeTemplateId),
    [allTemplates, activeTemplateId],
  );

  /** Interpolate parameters into the active template SQL */
  const interpolatedSql = useMemo(() => {
    if (!activeTemplate) {
      return sql;
    }
    let interpolated = activeTemplate.sql;
    for (const param of activeTemplate.parameters) {
      const value = paramValues[param.name] ?? param.defaultValue;
      interpolated = interpolated.replace(
        new RegExp(`\\{\\{${param.name}\\}\\}`, "g"),
        value,
      );
    }
    return interpolated;
  }, [activeTemplate, paramValues, sql]);

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

  const handleRun = useCallback(() => {
    runQuery(interpolatedSql);
  }, [runQuery, interpolatedSql]);

  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      runQuery(interpolatedSql);
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
  }, [sql, runQuery, interpolatedSql]);

  const handleSelectTemplate = useCallback((templateId: string) => {
    const tmpl = allTemplates.find((template) => template.id === templateId);
    if (!tmpl) {
      return;
    }
    setActiveTemplateId(templateId);
    setActiveVisualization(tmpl.visualization);
    setResult(undefined);
    setError(undefined);

    /** Populate default param values */
    const defaults: Record<string, string> = {};
    for (const param of tmpl.parameters) {
      defaults[param.name] = param.defaultValue;
    }
    setParamValues(defaults);

    /** Populate the editor with the raw SQL (with {{placeholders}} visible) */
    setSql(tmpl.sql);
    textareaRef.current?.focus();
  }, [allTemplates]);

  const handleClearTemplate = useCallback(() => {
    setActiveTemplateId(undefined);
    setParamValues({});
    setActiveVisualization("table");
  }, []);

  const handleSaveTemplate = useCallback(() => {
    if (saveTemplateName.trim().length === 0 || sql.trim().length === 0) {
      return;
    }
    const newTemplate: QueryTemplate = {
      id: `saved-${Date.now()}`,
      name: saveTemplateName.trim(),
      description: "User-saved template",
      sql: sql.trim(),
      parameters: [],
      visualization: "table",
    };
    setSavedTemplates([...savedTemplates, newTemplate]);
    setSaveTemplateName("");
    setShowSaveForm(false);
  }, [saveTemplateName, sql, savedTemplates]);

  const handleDeleteSavedTemplate = useCallback((templateId: string) => {
    setSavedTemplates(savedTemplates.filter((tmpl) => tmpl.id !== templateId));
    if (activeTemplateId === templateId) {
      setActiveTemplateId(undefined);
    }
  }, [savedTemplates, activeTemplateId]);

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
      {/* Templates sidebar (left) */}
      <div className="flex w-[260px] shrink-0 flex-col overflow-y-auto border-r border-envoi-border bg-envoi-surface">
        <div className="flex items-center justify-between border-b border-envoi-border px-[12px] py-[6px]">
          <span className="text-[13px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Templates
          </span>
          <button
            onClick={() => setShowSaveForm((prev) => !prev)}
            className="flex items-center gap-[4px] rounded px-[6px] py-[2px] text-[13px] text-envoi-text-muted transition-colors hover:bg-envoi-border-light hover:text-envoi-text"
            title="Save current SQL as template"
          >
            <Plus size={12} />
          </button>
        </div>

        {/* Save form */}
        {showSaveForm && (
          <div className="flex flex-col gap-[6px] border-b border-envoi-border px-[12px] py-[8px]">
            <input
              type="text"
              value={saveTemplateName}
              onChange={(event) => setSaveTemplateName(event.target.value)}
              placeholder="Template name..."
              className="rounded border border-envoi-border-light bg-envoi-bg px-[8px] py-[4px] text-[12px] text-envoi-text outline-none focus:border-envoi-accent"
            />
            <button
              onClick={handleSaveTemplate}
              disabled={saveTemplateName.trim().length === 0 || sql.trim().length === 0}
              className="flex items-center justify-center gap-[4px] rounded bg-envoi-accent px-[8px] py-[4px] text-[13px] font-semibold text-white transition-opacity disabled:opacity-40"
            >
              <BookmarkPlus size={12} />
              Save Template
            </button>
          </div>
        )}

        {/* Built-in templates */}
        <div className="border-b border-envoi-border px-[12px] py-[4px]">
          <span className="text-[12px] font-semibold uppercase tracking-[0.06em] text-envoi-text-dim">
            Built-in
          </span>
        </div>
        {builtinTemplates.map((tmpl) => (
          <button
            key={tmpl.id}
            onClick={() => handleSelectTemplate(tmpl.id)}
            className={`border-b border-envoi-border-light px-[12px] py-[8px] text-left transition-colors ${
              tmpl.id === activeTemplateId
                ? "border-l-[3px] border-l-envoi-accent bg-envoi-bg"
                : "border-l-[3px] border-l-transparent hover:bg-envoi-border-light"
            }`}
          >
            <div className="text-[12px] font-semibold text-envoi-text">
              {tmpl.name}
            </div>
            <div className="mt-[2px] text-[13px] text-envoi-text-dim">
              {tmpl.description}
            </div>
          </button>
        ))}

        {/* User-saved templates */}
        {savedTemplates.length > 0 && (
          <>
            <div className="border-b border-envoi-border px-[12px] py-[4px]">
              <span className="text-[12px] font-semibold uppercase tracking-[0.06em] text-envoi-text-dim">
                Saved
              </span>
            </div>
            {savedTemplates.map((tmpl) => (
              <div
                key={tmpl.id}
                className={`flex items-start border-b border-envoi-border-light transition-colors ${
                  tmpl.id === activeTemplateId
                    ? "border-l-[3px] border-l-envoi-accent bg-envoi-bg"
                    : "border-l-[3px] border-l-transparent hover:bg-envoi-border-light"
                }`}
              >
                <button
                  onClick={() => handleSelectTemplate(tmpl.id)}
                  className="flex-1 px-[12px] py-[8px] text-left"
                >
                  <div className="text-[12px] font-semibold text-envoi-text">
                    {tmpl.name}
                  </div>
                </button>
                <button
                  onClick={() => handleDeleteSavedTemplate(tmpl.id)}
                  className="shrink-0 px-[8px] py-[10px] text-envoi-text-dim transition-colors hover:text-envoi-red"
                  title="Delete saved template"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </>
        )}

        {builtinTemplates.length === 0 && savedTemplates.length === 0 && (
          <div className="px-[12px] py-[10px] text-[12px] text-envoi-text-dim">
            No templates available
          </div>
        )}
      </div>

      {/* Main area: editor + results */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Parameter bar (when a template with params is active) */}
        {activeTemplate && activeTemplate.parameters.length > 0 && (
          <div className="flex items-end gap-[10px] border-b border-envoi-border px-[14px] py-[8px]">
            {activeTemplate.parameters.map((param) => (
              <div key={param.name} className="flex flex-col gap-[4px]">
                <label className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                  {param.label}
                </label>
                <input
                  type="text"
                  value={paramValues[param.name] ?? param.defaultValue}
                  onChange={(event) =>
                    setParamValues((prev) => ({
                      ...prev,
                      [param.name]: event.target.value,
                    }))
                  }
                  className="rounded border border-envoi-border-light bg-envoi-bg px-[8px] py-[4px] text-[12px] text-envoi-text outline-none focus:border-envoi-accent"
                  style={{ fontFamily: T.mono, width: 200 }}
                />
              </div>
            ))}
            <button
              onClick={handleClearTemplate}
              className="rounded px-[8px] py-[5px] text-[13px] text-envoi-text-muted transition-colors hover:bg-envoi-border-light hover:text-envoi-text"
            >
              Clear
            </button>
          </div>
        )}

        {/* SQL Editor */}
        <div className="flex flex-col gap-[8px] border-b border-envoi-border px-[14px] py-[10px]">
          <textarea
            ref={textareaRef}
            value={activeTemplate ? interpolatedSql : sql}
            onChange={(event) => {
              setSql(event.target.value);
              if (activeTemplate) {
                setActiveTemplateId(undefined);
                setParamValues({});
              }
            }}
            onKeyDown={handleKeyDown}
            placeholder="Enter SQL query... (Cmd+Enter to run)"
            className="min-h-[120px] w-full resize-y rounded border border-envoi-border-light bg-envoi-surface px-[10px] py-[8px] text-[12px] leading-[20px] text-envoi-text outline-none focus:border-envoi-accent"
            style={{ fontFamily: T.mono }}
          />
          <div className="flex items-center gap-[8px]">
            <button
              onClick={handleRun}
              disabled={isLoading || interpolatedSql.trim().length === 0}
              className="flex items-center gap-[6px] rounded bg-envoi-accent px-[12px] py-[5px] text-[13px] font-semibold text-white transition-opacity disabled:opacity-40"
            >
              {isLoading ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Play size={12} />
              )}
              Run Query
            </button>
            <span className="text-[13px] text-envoi-text-dim">
              Cmd+Enter
            </span>
          </div>
        </div>

        {/* Results area */}
        <div className="flex-1 overflow-auto">
          {error && (
            <div
              className="border-b px-[14px] py-[10px] text-[12px]"
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
                <span className="text-[13px] text-envoi-text-dim">
                  {result.rowCount} rows
                </span>
                <span className="text-[13px] text-envoi-text-dim">
                  {result.durationMs}ms
                </span>
              </div>

              {/* Visualization (if template is active and has chart type) */}
              {activeTemplate && activeVisualization === "bar" && result.columns.length >= 2 && (
                <div className="px-[14px] py-[14px]">
                  <BarChart result={result} />
                </div>
              )}
              {activeTemplate && activeVisualization === "line" && result.columns.length >= 2 && (
                <div className="px-[14px] py-[14px]">
                  <LineChart result={result} />
                </div>
              )}

              {/* Table */}
              {result.columns.length > 0 && (
                <div className="overflow-auto">
                  <table className="w-full border-collapse text-[12px]">
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
            <div className="flex flex-1 items-center justify-center py-[40px] text-[12px] text-envoi-text-dim">
              Select a template or write SQL, then click Run
            </div>
          )}
        </div>
      </div>

      {/* Schema sidebar (right) */}
      <div className="flex w-[240px] shrink-0 flex-col overflow-y-auto border-l border-envoi-border bg-envoi-surface">
        <div className="border-b border-envoi-border px-[12px] py-[6px]">
          <span className="text-[13px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Schema
          </span>
        </div>
        {Array.from(tables.entries()).map(([tableName, columns]) => (
          <SchemaTable key={tableName} tableName={tableName} columns={columns} />
        ))}
        {tables.size === 0 && (
          <div className="px-[12px] py-[10px] text-[12px] text-envoi-text-dim">
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
        className="flex w-full items-center gap-[6px] px-[12px] py-[6px] text-left text-[12px] font-semibold text-envoi-text hover:bg-envoi-border-light"
      >
        <ChevronIcon size={12} className="text-envoi-text-dim" />
        {tableName}
        <span className="text-envoi-text-dim">({columns.length})</span>
      </button>
      {expanded && (
        <div className="pb-[4px]">
          {columns.map((col) => (
            <div
              key={col.columnName}
              className="flex items-center gap-[6px] px-[24px] py-[2px] text-[13px]"
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

/** Simple SVG bar chart */
function BarChart({ result }: { result: QueryResult }) {
  const labelCol = result.columns[0];
  const valueCol = result.columns.length > 2 ? result.columns[2] : result.columns[1];
  if (!labelCol || !valueCol) {
    return undefined;
  }

  const data = result.rows.map((row) => ({
    label: String(row[labelCol] ?? ""),
    value: Number(row[valueCol] ?? 0),
  }));

  const maxValue = Math.max(...data.map((point) => point.value), 0.01);
  const barWidth = Math.max(8, Math.min(40, BAR_PLOT_W / data.length - 4));

  return (
    <div className="mb-[14px]">
      <svg viewBox={`0 0 ${BAR_VIEW_W} ${BAR_VIEW_H}`} className="w-full" style={{ maxWidth: BAR_VIEW_W }}>
        <text
          x={BAR_MARGIN.left - 8}
          y={BAR_MARGIN.top}
          textAnchor="end"
          style={{ fontSize: "10px", fill: T.textDim }}
        >
          {maxValue <= 1 ? "100%" : maxValue.toFixed(0)}
        </text>
        <text
          x={BAR_MARGIN.left - 8}
          y={BAR_MARGIN.top + BAR_PLOT_H}
          textAnchor="end"
          style={{ fontSize: "10px", fill: T.textDim }}
        >
          0
        </text>

        {data.map((point, barIndex) => {
          const barX = BAR_MARGIN.left + (barIndex / data.length) * BAR_PLOT_W + (BAR_PLOT_W / data.length - barWidth) / 2;
          const barHeight = (point.value / maxValue) * BAR_PLOT_H;
          const barY = BAR_MARGIN.top + BAR_PLOT_H - barHeight;

          return (
            <g key={barIndex}>
              <rect
                x={barX}
                y={barY}
                width={barWidth}
                height={barHeight}
                fill={T.accent}
                rx={2}
              />
              <text
                x={barX + barWidth / 2}
                y={BAR_MARGIN.top + BAR_PLOT_H + 14}
                textAnchor="middle"
                style={{ fontSize: "9px", fill: T.textDim }}
              >
                {point.label.length > 12 ? `${point.label.slice(0, 12)}...` : point.label}
              </text>
            </g>
          );
        })}

        <line
          x1={BAR_MARGIN.left}
          y1={BAR_MARGIN.top + BAR_PLOT_H}
          x2={BAR_MARGIN.left + BAR_PLOT_W}
          y2={BAR_MARGIN.top + BAR_PLOT_H}
          stroke={T.borderLight}
          strokeWidth={1}
        />
      </svg>
    </div>
  );
}

/** Simple SVG line chart */
function LineChart({ result }: { result: QueryResult }) {
  const xCol = result.columns[0];
  const yCol = result.columns[1];
  if (!xCol || !yCol) {
    return undefined;
  }

  const data = result.rows.map((row) => ({
    x: Number(row[xCol] ?? 0),
    y: Number(row[yCol] ?? 0),
  }));

  if (data.length < 2) {
    return undefined;
  }

  const xMin = Math.min(...data.map((point) => point.x));
  const xMax = Math.max(...data.map((point) => point.x));
  const yMax = Math.max(...data.map((point) => point.y), 0.01);
  const xRange = xMax - xMin || 1;

  const toX = (value: number) => LINE_MARGIN.left + ((value - xMin) / xRange) * LINE_PLOT_W;
  const toY = (value: number) => LINE_MARGIN.top + LINE_PLOT_H - (value / yMax) * LINE_PLOT_H;

  const pathD = data
    .map((point, pointIndex) => `${pointIndex === 0 ? "M" : "L"}${toX(point.x).toFixed(1)},${toY(point.y).toFixed(1)}`)
    .join(" ");

  return (
    <div className="mb-[14px]">
      <svg viewBox={`0 0 ${LINE_VIEW_W} ${LINE_VIEW_H}`} className="w-full" style={{ maxWidth: LINE_VIEW_W }}>
        <line
          x1={LINE_MARGIN.left}
          y1={LINE_MARGIN.top + LINE_PLOT_H}
          x2={LINE_MARGIN.left + LINE_PLOT_W}
          y2={LINE_MARGIN.top + LINE_PLOT_H}
          stroke={T.borderLight}
          strokeWidth={1}
        />

        <text x={LINE_MARGIN.left - 8} y={LINE_MARGIN.top + 3} textAnchor="end" style={{ fontSize: "10px", fill: T.textDim }}>
          {yMax.toFixed(0)}
        </text>
        <text x={LINE_MARGIN.left - 8} y={LINE_MARGIN.top + LINE_PLOT_H + 3} textAnchor="end" style={{ fontSize: "10px", fill: T.textDim }}>
          0
        </text>

        <path d={pathD} fill="none" stroke={T.accent} strokeWidth={1.5} />

        {data.map((point, dotIndex) => (
          <circle key={dotIndex} cx={toX(point.x)} cy={toY(point.y)} r={2.5} fill={T.accent} />
        ))}
      </svg>
    </div>
  );
}

/** Format a cell value for display */
function formatValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "NULL";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
