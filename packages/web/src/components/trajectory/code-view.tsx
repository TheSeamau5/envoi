/**
 * Syntax-highlighted code viewer with diff markers.
 * Client component — renders code lines with line numbers and highlights.
 *
 * Syntax highlighting for Rust — colors from T.syntax* tokens:
 * - Keywords (fn, let, mut, pub, use, mod, struct, impl, etc.)
 * - Types (String, Vec, Option, Result, bool, i64, usize, etc.)
 * - Numbers
 * - Comments (//)
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

/** Rust keywords to highlight */
const KEYWORDS = new Set([
  "fn", "let", "mut", "pub", "use", "mod", "struct", "impl",
  "if", "else", "match", "return", "for", "while", "loop",
  "break", "continue", "const", "static", "type", "enum",
  "trait", "where", "as", "in", "ref", "self", "super",
  "crate", "async", "await", "move", "unsafe", "extern",
]);

/** Rust types to highlight */
const TYPES = new Set([
  "String", "Vec", "Option", "Result", "bool", "i64", "usize",
  "i8", "i16", "i32", "u8", "u16", "u32", "u64", "f32", "f64",
  "isize", "str", "char", "Box", "Rc", "Arc", "HashMap", "HashSet",
  "Self",
]);

/** Tokenize and colorize a single line of Rust code */
function highlightLine(line: string): React.ReactNode[] {
  /** Check if line is a comment */
  const trimmed = line.trimStart();
  if (trimmed.startsWith("//")) {
    const leadingSpace = line.slice(0, line.length - trimmed.length);
    return [
      <span key="ws">{leadingSpace}</span>,
      <span key="comment" style={{ color: T.syntaxComment }}>{trimmed}</span>,
    ];
  }

  const tokens: React.ReactNode[] = [];
  /** Regex to split into words, numbers, strings, and other characters */
  const tokenRegex = /("(?:[^"\\]|\\.)*"|'[^']*'|\/\/.*$|\b\d+\b|\b[a-zA-Z_]\w*\b|[^\s\w]+|\s+)/g;
  let tokenMatch: RegExpExecArray | undefined;
  let tokenIndex = 0;

  tokenMatch = tokenRegex.exec(line) ?? undefined;
  while (tokenMatch !== undefined) {
    const token = tokenMatch[0];

    if (token.startsWith("//")) {
      tokens.push(
        <span key={tokenIndex} style={{ color: T.syntaxComment }}>{token}</span>,
      );
    } else if (token.startsWith('"') || token.startsWith("'")) {
      tokens.push(
        <span key={tokenIndex} style={{ color: T.syntaxKeyword }}>{token}</span>,
      );
    } else if (/^\d+$/.test(token)) {
      tokens.push(
        <span key={tokenIndex} style={{ color: T.syntaxNumber }}>{token}</span>,
      );
    } else if (KEYWORDS.has(token)) {
      tokens.push(
        <span key={tokenIndex} style={{ color: T.syntaxKeyword }}>{token}</span>,
      );
    } else if (TYPES.has(token)) {
      tokens.push(
        <span key={tokenIndex} style={{ color: T.syntaxType }}>{token}</span>,
      );
    } else {
      tokens.push(<span key={tokenIndex}>{token}</span>);
    }

    tokenIndex++;
    tokenMatch = tokenRegex.exec(line) ?? undefined;
  }

  return tokens;
}

export function CodeView({ snapshot, filePath, additions, deletions }: CodeViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [flashing, setFlashing] = useState(false);

  /** Scroll to first added line and trigger flash animation on file change */
  useEffect(() => {
    if (!scrollRef.current || !snapshot) {
      return undefined;
    }

    const firstAdded = snapshot.added.length > 0 ? snapshot.added[0] : undefined;
    if (firstAdded !== undefined) {
      /** Scroll to first changed line, centered in viewport */
      const scrollTarget = Math.max(0, firstAdded * LINE_HEIGHT - scrollRef.current.clientHeight / 3);
      scrollRef.current.scrollTop = scrollTarget;

      /** Trigger flash animation */
      setFlashing(true);
      const timer = setTimeout(() => {
        setFlashing(false);
      }, 800);
      return () => {
        clearTimeout(timer);
      };
    }

    /** No added lines — scroll to top */
    scrollRef.current.scrollTop = 0;
    return undefined;
  }, [filePath, snapshot]);

  if (!snapshot || !filePath) {
    return (
      <div className="flex flex-1 items-center justify-center text-[11px] text-envoi-text-dim">
        Select a file to view its contents
      </div>
    );
  }

  const addedSet = new Set(snapshot.added);

  const hasStats = (additions !== undefined && additions > 0) ||
    (deletions !== undefined && deletions > 0);

  return (
    <div ref={scrollRef} className="flex flex-1 flex-col overflow-auto">
      {/* File diff stats */}
      {hasStats && (
        <div
          className="sticky top-0 z-10 flex items-center gap-[8px] border-b px-[12px] py-[4px]"
          style={{
            borderColor: T.borderLight,
            background: T.surface,
          }}
        >
          {additions !== undefined && additions > 0 && (
            <span
              className="text-[10px] font-semibold"
              style={{ color: T.diffAddedBorder }}
            >
              +{additions}
            </span>
          )}
          {deletions !== undefined && deletions > 0 && (
            <span
              className="text-[10px] font-semibold"
              style={{ color: T.red }}
            >
              &minus;{deletions}
            </span>
          )}
        </div>
      )}
      <div style={{ fontFamily: "var(--font-mono), 'JetBrains Mono', monospace" }}>
        {snapshot.lines.map((line, lineIndex) => {
          const isAdded = addedSet.has(lineIndex);
          return (
            <div
              key={lineIndex}
              className="flex"
              style={{
                background: isAdded
                  ? (flashing ? T.diffFlashBg : T.diffAddedBg)
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
                  fontSize: 10,
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
                  fontSize: 10,
                  lineHeight: "20px",
                  color: isAdded ? T.diffAddedBorder : "transparent",
                }}
              >
                {isAdded ? "+" : " "}
              </div>

              {/* Code content */}
              <pre
                className="flex-1 whitespace-pre"
                style={{
                  fontSize: 11,
                  lineHeight: "20px",
                  color: T.text,
                  margin: 0,
                  padding: 0,
                }}
              >
                {highlightLine(line)}
              </pre>
            </div>
          );
        })}
      </div>
    </div>
  );
}
