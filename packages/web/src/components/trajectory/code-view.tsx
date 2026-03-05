/**
 * Syntax-highlighted code viewer with diff markers.
 * Client component — renders code lines with line numbers and highlights.
 *
 * Uses Shiki for syntax highlighting with language detection from file path.
 * Unknown/unsupported extensions fall back to Rust.
 *
 * Added lines get green background + green left border (3px) + "+" marker.
 * On file change, scrolls to the first changed line and flashes added lines.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import type { FileSnapshot } from "@/lib/types";
import { T } from "@/lib/tokens";

type CodeViewProps = {
  snapshot?: FileSnapshot;
  filePath?: string;
  additions?: number;
  deletions?: number;
};

/** Line height in pixels — used for scroll offset calculation */
const LINE_HEIGHT = 20;
/** Soft wrap column for code display */
const WRAP_COLUMN = 120;

const SHIKI_THEME: import("shiki").BundledTheme = "github-light-default";
const DEFAULT_LANGUAGE: import("shiki").BundledLanguage = "rust";
const FONT_STYLE_ITALIC = 1;
const FONT_STYLE_BOLD = 2;
const FONT_STYLE_UNDERLINE = 4;

type ShikiModule = typeof import("shiki");
type ShikiBundledLanguage = import("shiki").BundledLanguage;
type ShikiHighlighter = Awaited<
  ReturnType<ShikiModule["getSingletonHighlighter"]>
>;
type HighlightToken = {
  content: string;
  color?: string;
  bgColor?: string;
  fontStyle?: number;
};
type HighlightResult = {
  filePath: string;
  snapshot: FileSnapshot;
  lines: HighlightToken[][];
};

let shikiModulePromise: Promise<ShikiModule> | undefined;
let shikiHighlighterPromise: Promise<ShikiHighlighter> | undefined;

function getShikiModule(): Promise<ShikiModule> {
  shikiModulePromise ??= import("shiki");
  return shikiModulePromise;
}

async function getShikiHighlighter(): Promise<ShikiHighlighter> {
  if (!shikiHighlighterPromise) {
    const shiki = await getShikiModule();
    shikiHighlighterPromise = shiki.getSingletonHighlighter({
      themes: [SHIKI_THEME],
      langs: [DEFAULT_LANGUAGE],
      engine: shiki.createJavaScriptRegexEngine(),
    });
  }
  return shikiHighlighterPromise;
}

function splitFilePath(filePath: string): {
  basename: string;
  extension?: string;
} {
  const basename =
    filePath.split("/").pop()?.toLowerCase() ?? filePath.toLowerCase();
  const extension = basename.includes(".")
    ? basename.split(".").pop()?.toLowerCase()
    : undefined;
  return { basename, extension };
}

function hasBundledLanguage(
  shiki: ShikiModule,
  candidate: string,
): candidate is ShikiBundledLanguage {
  return (
    Object.prototype.hasOwnProperty.call(shiki.bundledLanguages, candidate) ||
    Object.prototype.hasOwnProperty.call(shiki.bundledLanguagesAlias, candidate)
  );
}

function resolveLanguageFromPath(
  filePath: string,
  shiki: ShikiModule,
): ShikiBundledLanguage {
  const { basename, extension } = splitFilePath(filePath);
  const candidates = new Set<string>([basename]);
  const firstSegment = basename.split(".")[0];
  if (firstSegment) {
    candidates.add(firstSegment);
  }

  if (extension) {
    candidates.add(extension);
  }

  if (basename.endsWith(".d.ts")) {
    candidates.add("ts");
  }

  if (basename.endsWith(".d.tsx")) {
    candidates.add("tsx");
  }

  for (const candidate of candidates) {
    if (hasBundledLanguage(shiki, candidate)) {
      return candidate;
    }
  }

  return DEFAULT_LANGUAGE;
}

function buildTokenStyle(
  token: HighlightToken,
): React.CSSProperties | undefined {
  const fontStyle = token.fontStyle ?? 0;
  const hasStyle = token.color !== undefined || fontStyle !== 0;

  if (!hasStyle) {
    return undefined;
  }

  return {
    color: token.color,
    fontStyle: (fontStyle & FONT_STYLE_ITALIC) !== 0 ? "italic" : undefined,
    fontWeight: (fontStyle & FONT_STYLE_BOLD) !== 0 ? 700 : undefined,
    textDecoration:
      (fontStyle & FONT_STYLE_UNDERLINE) !== 0 ? "underline" : undefined,
  };
}

function renderHighlightedLine(
  line: string,
  highlightedLine: HighlightToken[] | undefined,
): React.ReactNode {
  if (!highlightedLine || highlightedLine.length === 0) {
    return line;
  }

  return highlightedLine.map((token, tokenIndex) => (
    <span key={tokenIndex} style={buildTokenStyle(token)}>
      {token.content}
    </span>
  ));
}

export function CodeView({
  snapshot,
  filePath,
  additions,
  deletions,
}: CodeViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [flashing, setFlashing] = useState(false);
  const [highlightResult, setHighlightResult] = useState<
    HighlightResult | undefined
  >(undefined);

  /** Track previous props for change detection */
  const prevFileRef = useRef(filePath);
  const prevSnapshotRef = useRef(snapshot);
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const flashFrameRef = useRef<number | undefined>(undefined);
  const highlightRequestRef = useRef(0);

  useEffect(() => {
    if (!snapshot || !filePath) {
      return;
    }

    const requestId = highlightRequestRef.current + 1;
    highlightRequestRef.current = requestId;
    let cancelled = false;

    const code = snapshot.lines.join("\n");

    void (async () => {
      const shiki = await getShikiModule();
      const highlighter = await getShikiHighlighter();
      const language = resolveLanguageFromPath(filePath, shiki);
      const canonicalLanguage = highlighter.resolveLangAlias(language);

      if (!highlighter.getLoadedLanguages().includes(canonicalLanguage)) {
        await highlighter.loadLanguage(language);
      }

      const highlighted = highlighter.codeToTokens(code, {
        lang: language,
        theme: SHIKI_THEME,
      }).tokens as HighlightToken[][];

      if (cancelled || requestId !== highlightRequestRef.current) {
        return;
      }

      setHighlightResult({
        filePath,
        snapshot,
        lines: highlighted,
      });
    })().catch((error) => {
      console.error("Failed to highlight code with Shiki", error);
      if (cancelled || requestId !== highlightRequestRef.current) {
        return;
      }
      setHighlightResult(undefined);
    });

    return () => {
      cancelled = true;
    };
  }, [snapshot, filePath]);

  /** Detect file/snapshot changes — scroll + flash after render */
  useEffect(() => {
    const fileChanged = prevFileRef.current !== filePath;
    const snapshotChanged = prevSnapshotRef.current !== snapshot;

    if (!fileChanged && !snapshotChanged) {
      return;
    }

    prevFileRef.current = filePath;
    prevSnapshotRef.current = snapshot;

    if (flashTimerRef.current !== undefined) {
      clearTimeout(flashTimerRef.current);
    }
    if (flashFrameRef.current !== undefined) {
      cancelAnimationFrame(flashFrameRef.current);
    }

    const container = scrollRef.current;
    if (!container || !snapshot) {
      return;
    }

    const firstAdded =
      snapshot.added.length > 0 ? snapshot.added[0] : undefined;
    if (firstAdded === undefined) {
      container.scrollTop = 0;
      return;
    }

    const scrollTarget = Math.max(
      0,
      firstAdded * LINE_HEIGHT - container.clientHeight / 3,
    );
    container.scrollTop = scrollTarget;

    flashFrameRef.current = requestAnimationFrame(() => {
      setFlashing(true);
    });
    flashTimerRef.current = setTimeout(() => {
      setFlashing(false);
    }, 800);
  }, [filePath, snapshot]);

  /** Cleanup only — no setState in this effect */
  useEffect(() => {
    return () => {
      if (flashTimerRef.current !== undefined) {
        clearTimeout(flashTimerRef.current);
      }
      if (flashFrameRef.current !== undefined) {
        cancelAnimationFrame(flashFrameRef.current);
      }
    };
  }, []);

  if (!snapshot || !filePath) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-envoi-text-dim">
        Select a file to view its contents
      </div>
    );
  }

  const addedSet = new Set(snapshot.added);

  const hasStats =
    (additions !== undefined && additions > 0) ||
    (deletions !== undefined && deletions > 0);
  const activeHighlightedLines =
    highlightResult &&
    highlightResult.filePath === filePath &&
    highlightResult.snapshot === snapshot
      ? highlightResult.lines
      : undefined;

  return (
    <div
      ref={scrollRef}
      className="flex flex-1 flex-col overflow-y-auto overflow-x-hidden"
    >
      {/* File diff stats */}
      {hasStats && (
        <div
          className="sticky top-0 z-10 flex items-center gap-2 border-b px-3 py-1"
          style={{
            borderColor: T.borderLight,
            background: T.surface,
          }}
        >
          {additions !== undefined && additions > 0 && (
            <span
              className="text-[12px] font-semibold"
              style={{ color: T.diffAddedBorder }}
            >
              +{additions}
            </span>
          )}
          {deletions !== undefined && deletions > 0 && (
            <span
              className="text-[12px] font-semibold"
              style={{ color: T.red }}
            >
              &minus;{deletions}
            </span>
          )}
        </div>
      )}
      <div
        style={{ fontFamily: "var(--font-mono), 'JetBrains Mono', monospace" }}
      >
        {snapshot.lines.map((line, lineIndex) => {
          const isAdded = addedSet.has(lineIndex);
          return (
            <div
              key={lineIndex}
              className="flex"
              style={{
                background: isAdded
                  ? flashing
                    ? T.diffFlashBg
                    : T.diffAddedBg
                  : undefined,
                borderLeft: isAdded
                  ? `3px solid ${T.diffAddedBorder}`
                  : "3px solid transparent",
                transition: isAdded ? "background 0.6s ease-out" : undefined,
              }}
            >
              {/* Line number gutter */}
              <div
                className="shrink-0 select-none text-right text-envoi-text-dim"
                style={{
                  width: 44,
                  padding: "0 8px 0 0",
                  fontSize: 12,
                  lineHeight: "20px",
                }}
              >
                {lineIndex + 1}
              </div>

              {/* Added marker */}
              <div
                className="shrink-0 select-none text-center"
                style={{
                  width: 16,
                  fontSize: 12,
                  lineHeight: "20px",
                  color: isAdded ? T.diffAddedBorder : "transparent",
                }}
              >
                {isAdded ? "+" : " "}
              </div>

              {/* Code content */}
              <pre
                className="min-w-0 flex-1 whitespace-pre-wrap"
                style={{
                  maxWidth: `${WRAP_COLUMN}ch`,
                  fontSize: 13,
                  lineHeight: "20px",
                  color: T.text,
                  margin: 0,
                  padding: 0,
                  overflowWrap: "anywhere",
                  wordBreak: "break-word",
                }}
              >
                {renderHighlightedLine(
                  line,
                  activeHighlightedLines?.[lineIndex],
                )}
              </pre>
            </div>
          );
        })}
      </div>
    </div>
  );
}
