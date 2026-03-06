/**
 * Chat message list — always renders a flex-1 overflow-y-auto container.
 * This div MUST always exist with flex-1 so the input stays pinned to bottom.
 * Matches the Edfinity test area pattern exactly.
 */

"use client";

import { useRef, useCallback } from "react";
import { useChatContext } from "./chat-provider";
import { ChatMessage } from "./chat-message";

/** Scrollable message list — always flex-1 so input stays at bottom */
export function ChatMessages() {
  const { messages, hasMessages, hasHydrated, isStreaming } = useChatContext();
  const containerRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);

  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    userScrolledRef.current = distanceFromBottom > 40;
  }, []);

  const scrollSentinelRef = useCallback(
    (node: HTMLDivElement | null) => {
      if (node && !userScrolledRef.current) {
        node.scrollIntoView({ behavior: "smooth", block: "end" });
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [messages, isStreaming],
  );

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-3 py-4"
    >
      {!hasHydrated && hasMessages ? (
        <div className="flex h-full flex-col items-center justify-center text-center">
          <p className="text-[12px] text-envoi-text-dim">
            Loading conversation...
          </p>
        </div>
      ) : messages.length === 0 ? (
        <div className="flex h-full flex-col items-center justify-center text-center">
          <p className="text-[12px] text-envoi-text-dim">
            Test your questions here.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {messages.map((message) => (
            <ChatMessage key={message.id} message={message} />
          ))}
          <div ref={scrollSentinelRef} />
        </div>
      )}
    </div>
  );
}
