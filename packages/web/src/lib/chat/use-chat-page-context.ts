/**
 * Hook for pages to report their current context to the chat system.
 * Call this in page-level client components so the chat agent
 * knows what the user is currently viewing.
 */

"use client";

import { useEffect, useRef } from "react";
import { useChatContext } from "@/components/chat/chat-provider";
import type { ChatPageContext } from "./types";

/** Report the current page context to the chat provider */
export function useChatPageContext(context: ChatPageContext) {
  const { setPageContext } = useChatContext();
  const serialized = JSON.stringify(context);
  const prevRef = useRef(serialized);

  /**
   * Update context when it changes. This is one of the rare acceptable
   * useEffect patterns — syncing external state (chat context) with
   * page-level props. The setter is stable, so this only fires on
   * actual context changes.
   */
  useEffect(() => {
    if (prevRef.current === serialized) {
      return;
    }
    prevRef.current = serialized;
    setPageContext(context);
  }, [context, serialized, setPageContext]);
}
