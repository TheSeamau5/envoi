/**
 * Chat context provider — manages conversation state, streaming,
 * page context, and drawer visibility. Lives in root Providers
 * so it persists across all page navigations.
 *
 * Exposes sendMessage / stopStreaming / clearConversation to children.
 * Streaming state is managed via refs to avoid stale closure bugs.
 */

"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useMemo,
  useEffect,
} from "react";
import type { ReactNode } from "react";
import type {
  ChatMessage,
  ChatPageContext,
  ContentBlock,
  ChatStreamEvent,
  ToolCallBlock,
} from "@/lib/chat/types";
import { usePersistedState } from "@/lib/storage";

type ChatContextValue = {
  messages: ChatMessage[];
  isStreaming: boolean;
  isOpen: boolean;
  hasUnread: boolean;
  pageContext: ChatPageContext;
  setPageContext: (context: ChatPageContext) => void;
  toggleOpen: () => void;
  setOpen: (open: boolean) => void;
  sendMessage: (text: string) => void;
  stopStreaming: () => void;
  clearConversation: () => void;
};

const ChatContext = createContext<ChatContextValue | undefined>(undefined);

/** Access chat context — must be inside ChatProvider */
export function useChatContext(): ChatContextValue {
  const context = useContext(ChatContext);
  if (!context) {
    throw new Error("useChatContext must be used within ChatProvider");
  }
  return context;
}

/** Generate a short random ID for messages */
function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

type ChatProviderProps = {
  children: ReactNode;
};

/** Root chat provider — wraps the entire app */
export function ChatProvider({ children }: ChatProviderProps) {
  const [messages, setMessages] = usePersistedState<ChatMessage[]>(
    "chat-messages",
    [],
  );
  const [isStreaming, setIsStreaming] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [hasUnread, setHasUnread] = useState(false);
  const [pageContext, setPageContext] = useState<ChatPageContext>({ page: "unknown" });

  const abortRef = useRef<AbortController>(undefined);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const isOpenRef = useRef(isOpen);
  isOpenRef.current = isOpen;

  const toggleOpen = useCallback(() => {
    setIsOpen((prev) => {
      if (!prev) {
        setHasUnread(false);
      }
      return !prev;
    });
  }, []);

  const setOpenDirect = useCallback((open: boolean) => {
    setIsOpen(open);
    if (open) {
      setHasUnread(false);
    }
  }, []);

  const clearConversation = useCallback(() => {
    setMessages([]);
    /** usePersistedState handles localStorage automatically */
  }, []);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  /** Process NDJSON stream from /api/chat */
  const processStream = useCallback(
    async (response: Response, assistantId: string) => {
      const reader = response.body?.getReader();
      if (!reader) {
        return;
      }
      const decoder = new TextDecoder();
      let buffer = "";

      /** Update the assistant message's blocks in state */
      const updateBlocks = (updater: (blocks: ContentBlock[]) => ContentBlock[]) => {
        setMessages((prev) => {
          const updated = prev.map((msg) => {
            if (msg.id === assistantId) {
              return { ...msg, blocks: updater(msg.blocks) };
            }
            return msg;
          });

          return updated;
        });
      };

      /** Track active tool calls by callId */
      const toolCallMap = new Map<string, number>();

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) {
              continue;
            }

            let event: ChatStreamEvent;
            try {
              event = JSON.parse(trimmed);
            } catch {
              continue;
            }

            switch (event.type) {
              case "text-delta":
                updateBlocks((blocks) => {
                  const last = blocks[blocks.length - 1];
                  if (last && last.type === "text") {
                    return [
                      ...blocks.slice(0, -1),
                      { ...last, text: last.text + event.text },
                    ];
                  }
                  return [...blocks, { type: "text" as const, text: event.text }];
                });
                break;

              case "tool-call-start":
                updateBlocks((blocks) => {
                  const toolBlock: ToolCallBlock = {
                    type: "tool-call",
                    toolName: event.toolName,
                    input: event.input,
                    status: "running",
                  };
                  toolCallMap.set(event.callId, blocks.length);
                  return [...blocks, toolBlock];
                });
                break;

              case "tool-call-done":
                updateBlocks((blocks) => {
                  const blockIndex = toolCallMap.get(event.callId);
                  if (blockIndex === undefined) {
                    return blocks;
                  }
                  return blocks.map((block, index) => {
                    if (index === blockIndex && block.type === "tool-call") {
                      return { ...block, output: event.output, status: "done" as const };
                    }
                    return block;
                  });
                });
                break;

              case "tool-call-error":
                updateBlocks((blocks) => {
                  const blockIndex = toolCallMap.get(event.callId);
                  if (blockIndex === undefined) {
                    return blocks;
                  }
                  return blocks.map((block, index) => {
                    if (index === blockIndex && block.type === "tool-call") {
                      return { ...block, output: event.error, status: "error" as const };
                    }
                    return block;
                  });
                });
                break;

              case "table":
                updateBlocks((blocks) => [
                  ...blocks,
                  {
                    type: "table" as const,
                    columns: event.columns,
                    rows: event.rows,
                    caption: event.caption,
                  },
                ]);
                break;

              case "image":
                updateBlocks((blocks) => [
                  ...blocks,
                  { type: "image" as const, src: event.src, alt: event.alt },
                ]);
                break;

              case "code":
                updateBlocks((blocks) => [
                  ...blocks,
                  {
                    type: "code" as const,
                    code: event.code,
                    language: event.language,
                    filename: event.filename,
                  },
                ]);
                break;

              case "error":
                updateBlocks((blocks) => [
                  ...blocks,
                  { type: "text" as const, text: `**Error:** ${event.message}` },
                ]);
                break;

              case "done":
                break;
            }
          }
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        updateBlocks((blocks) => [
          ...blocks,
          { type: "text" as const, text: "**Error:** Connection lost." },
        ]);
      }
    },
    [],
  );

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) {
        return;
      }

      const userMessage: ChatMessage = {
        id: generateId(),
        role: "user",
        blocks: [{ type: "text", text: trimmed }],
        timestamp: Date.now(),
      };

      const assistantMessage: ChatMessage = {
        id: generateId(),
        role: "assistant",
        blocks: [],
        timestamp: Date.now(),
      };

      const updatedMessages = [...messagesRef.current, userMessage, assistantMessage];
      setMessages(updatedMessages);
      setIsStreaming(true);

      if (!isOpenRef.current) {
        setHasUnread(true);
      }

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages: updatedMessages.slice(0, -1),
            pageContext,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const errorText = await response.text().catch(() => "Unknown error");
          setMessages((prev) => {
            const updated = prev.map((msg) => {
              if (msg.id === assistantMessage.id) {
                return {
                  ...msg,
                  blocks: [{ type: "text" as const, text: `**Error:** ${errorText}` }],
                };
              }
              return msg;
            });
  
            return updated;
          });
          return;
        }

        await processStream(response, assistantMessage.id);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setMessages((prev) => {
          const updated = prev.map((msg) => {
            if (msg.id === assistantMessage.id) {
              return {
                ...msg,
                blocks: [{ type: "text" as const, text: "**Error:** Failed to send message." }],
              };
            }
            return msg;
          });

          return updated;
        });
      } finally {
        setIsStreaming(false);
      }
    },
    [isStreaming, pageContext, processStream],
  );

  /** Global keyboard shortcut: Cmd+. to toggle chat */
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey && event.key === ".") {
        event.preventDefault();
        toggleOpen();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [toggleOpen]);

  const value = useMemo(
    () => ({
      messages,
      isStreaming,
      isOpen,
      hasUnread,
      pageContext,
      setPageContext,
      toggleOpen,
      setOpen: setOpenDirect,
      sendMessage,
      stopStreaming,
      clearConversation,
    }),
    [
      messages,
      isStreaming,
      isOpen,
      hasUnread,
      pageContext,
      toggleOpen,
      setOpenDirect,
      sendMessage,
      stopStreaming,
      clearConversation,
    ],
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
