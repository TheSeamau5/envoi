/**
 * Design tokens for inline styles (SVG charts, dynamic styling).
 * These mirror the Tailwind theme values in globals.css but are available
 * as plain strings for use in SVG elements and computed styles.
 */

/** Core design tokens — colors, radii, typography */
export const T = {
  bg: "#ffffff",
  surface: "#fafafa",
  border: "#e5e5e5",
  borderLight: "#f0f0f0",
  text: "#0a0a0a",
  textMuted: "#737373",
  textDim: "#a3a3a3",
  accent: "#f97316",
  accentDark: "#ea580c",
  accentBg: "rgba(249,115,22,0.06)",
  green: "#10b981",
  greenDark: "#059669",
  greenBg: "rgba(16,185,129,0.07)",
  red: "#ef4444",
  redDark: "#dc2626",
  redBg: "rgba(239,68,68,0.06)",
  gold: "#a17a08",
  goldBg: "rgba(161,122,8,0.07)",
  greenBgOpaque: "#f0fdf9",
  redBgOpaque: "#fef2f2",
  redBorderLight: "rgba(239,68,68,0.2)",
  /** Step type colors — match --color-step-* in globals.css */
  stepReasoning: "#f97316",
  stepRead: "#2563eb",
  stepWrite: "#059669",
  stepTool: "#a17a08",
  stepTest: "#059669",
  stepMcp: "#c026a3",
  stepText: "#6b7280",
  stepSpawn: "#7c3aed",
  stepMessage: "#0891b2",
  /** Syntax highlighting — match --color-syntax-* in globals.css */
  syntaxKeyword: "#c41a16",
  syntaxType: "#0b4f79",
  syntaxNumber: "#1750eb",
  syntaxComment: "#8e8e93",
  /** Code diff — match --color-diff-* in globals.css */
  diffAddedBg: "rgba(16,185,129,0.08)",
  diffAddedBorder: "#10b981",
  diffFlashBg: "rgba(16,185,129,0.25)",
  radius: "6px",
  mono: "var(--font-mono), 'JetBrains Mono', 'SF Mono', 'Menlo', monospace",
} as const;

/** Per-suite color definitions for charts and badges */
export const SUITE_COLORS: Record<string, { color: string; bg: string }> = {
  basics: { color: "#6d28d9", bg: "rgba(109,40,217,0.06)" },
  wacct: { color: "#2563eb", bg: "rgba(37,99,235,0.06)" },
  c_testsuite: { color: "#059669", bg: "rgba(5,150,105,0.06)" },
  torture: { color: "#dc2626", bg: "rgba(220,38,38,0.06)" },
};

/** Colors assigned to selected traces in Compare mode (max 4) */
export const TRACE_COLORS = [
  { line: "#0a0a0a", fill: "rgba(10,10,10,0.05)", label: "A" },
  { line: "#059669", fill: "rgba(5,150,105,0.05)", label: "B" },
  { line: "#dc2626", fill: "rgba(220,38,38,0.05)", label: "C" },
  { line: "#7c3aed", fill: "rgba(124,58,237,0.05)", label: "D" },
] as const;

/** Colors assigned to groups in Setup Compare mode (max 6) */
export const GROUP_COLORS = [
  { line: "#0a0a0a", fill: "rgba(10,10,10,0.04)" },
  { line: "#059669", fill: "rgba(5,150,105,0.04)" },
  { line: "#dc2626", fill: "rgba(220,38,38,0.04)" },
  { line: "#7c3aed", fill: "rgba(124,58,237,0.04)" },
  { line: "#2563eb", fill: "rgba(37,99,235,0.04)" },
  { line: "#ea580c", fill: "rgba(234,88,12,0.04)" },
] as const;
