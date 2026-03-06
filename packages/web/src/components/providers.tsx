/**
 * Client-side providers wrapper.
 * Wraps children with QueryClientProvider, TooltipProvider, and ChatProvider.
 * ChatProvider lives here so the chat drawer persists across all navigations.
 */

"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ChatProvider } from "@/components/chat/chat-provider";
import { ChatDrawer } from "@/components/chat/chat-drawer";
import { ChatToggle } from "@/components/chat/chat-toggle";

type ProvidersProps = {
  children: ReactNode;
  initialChatHasMessages: boolean;
};

/** Root providers — QueryClient, Tooltips, Chat */
export function Providers({
  children,
  initialChatHasMessages,
}: ProvidersProps) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5 * 60 * 1000,
            gcTime: 10 * 60 * 1000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={0}>
        <ChatProvider initialHasMessages={initialChatHasMessages}>
          <div className="flex h-full flex-1 overflow-hidden">
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              {children}
            </div>
            <ChatDrawer />
          </div>
          <ChatToggle />
        </ChatProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
