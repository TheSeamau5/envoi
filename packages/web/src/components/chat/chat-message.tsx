/**
 * Single chat message renderer.
 * User messages: right-aligned bubble with accent background.
 * Assistant messages: full-width with bot icon, copy button on hover.
 */

"use client";

import { useState } from "react";
import { Bot, Copy, Check } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/lib/chat/types";
import { ChatContentBlocks } from "./chat-content-blocks";

type ChatMessageProps = {
  message: ChatMessageType;
};

/** Extract plain text from all text blocks in a message */
function extractText(message: ChatMessageType): string {
  return message.blocks
    .filter((block) => block.type === "text")
    .map((block) => (block as { type: "text"; text: string }).text)
    .join("\n");
}

/** Render a single user or assistant message */
export function ChatMessage({ message }: ChatMessageProps) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";

  const handleCopy = () => {
    const text = extractText(message);
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-envoi-accent-bg px-3 py-2 text-[13px] text-envoi-text">
          <ChatContentBlocks blocks={message.blocks} />
        </div>
      </div>
    );
  }

  const hasTextContent = message.blocks.some((block) => block.type === "text");

  return (
    <div className="group flex gap-2">
      <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-envoi-surface">
        <Bot size={12} className="text-envoi-text-muted" />
      </div>
      <div className="min-w-0 flex-1 text-[13px] text-envoi-text">
        <ChatContentBlocks blocks={message.blocks} />
        {hasTextContent && (
          <button
            onClick={handleCopy}
            className="mt-1 rounded p-0.5 text-envoi-text-dim opacity-0 transition-opacity hover:text-envoi-text-muted group-hover:opacity-100"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        )}
      </div>
    </div>
  );
}
