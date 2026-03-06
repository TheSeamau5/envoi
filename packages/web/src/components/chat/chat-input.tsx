/**
 * Chat input — Edfinity-style unified container.
 * Bordered container with textarea on top, footer bar with helper text + send button.
 * Textarea auto-grows up to MAX_HEIGHT. Enter sends, Shift+Enter newlines.
 */

"use client";

import { useState, useRef, useCallback } from "react";
import { Send, Square } from "lucide-react";
import { useChatContext } from "./chat-provider";

const EMPTY_HINTS = [
  "Query data across trajectories",
  "Read and analyze logs",
  "Run Python analysis",
  "Generate charts and tables",
];

const MAX_HEIGHT = 160;

/** Auto-growing textarea with send/stop controls in a unified container */
export function ChatInput() {
  const { sendMessage, stopStreaming, isStreaming, messages } = useChatContext();
  const isEmpty = messages.length === 0;
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "auto";
    const scrollHeight = textarea.scrollHeight;
    textarea.style.height = `${Math.min(scrollHeight, MAX_HEIGHT)}px`;
  }, []);

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || isStreaming) {
      return;
    }
    sendMessage(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, isStreaming, sendMessage]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  const handleChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      setValue(event.target.value);
      adjustHeight();
    },
    [adjustHeight],
  );

  const canSend = value.trim().length > 0 && !isStreaming;

  return (
    <div className="shrink-0 border-t border-envoi-border px-3 py-3">
      {isEmpty && (
        <div className="mb-2 flex flex-wrap gap-1.5 px-0.5">
          {EMPTY_HINTS.map((hint) => (
            <span
              key={hint}
              className="rounded-full border border-envoi-border px-2.5 py-1 text-[11px] text-envoi-text-dim"
            >
              {hint}
            </span>
          ))}
        </div>
      )}
      <div className="overflow-hidden rounded-lg border border-envoi-border transition-all focus-within:border-envoi-text-dim">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
          rows={1}
          className="w-full resize-none bg-transparent px-3 py-2.5 text-[13px] text-envoi-text placeholder:text-envoi-text-dim focus:outline-none"
          style={{ maxHeight: MAX_HEIGHT, fontFamily: "inherit" }}
        />
        <div className="flex items-center justify-between border-t border-envoi-border-light px-3 py-1.5">
          <span className="text-[11px] text-envoi-text-dim">
            Enter to send, Shift+Enter for new line
          </span>
          {isStreaming ? (
            <button
              onClick={stopStreaming}
              className="flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-envoi-red transition-colors hover:bg-envoi-red-bg"
            >
              <Square size={10} />
              Stop
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!canSend}
              className="flex items-center justify-center rounded p-1 text-envoi-accent transition-colors hover:bg-envoi-accent-bg disabled:text-envoi-text-dim disabled:hover:bg-transparent"
            >
              <Send size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
