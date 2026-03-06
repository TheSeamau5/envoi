/**
 * Floating toggle button to open the chat drawer.
 * Shows an unread indicator dot when the assistant responds while closed.
 * Hidden when the drawer is already open.
 */

"use client";

import { MessageSquare } from "lucide-react";
import { useChatContext } from "./chat-provider";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** Floating button to open the chat panel */
export function ChatToggle() {
  const { isOpen, toggleOpen, hasUnread } = useChatContext();

  if (isOpen) {
    return null;
  }

  return (
    <div className="fixed bottom-4 right-4 z-50">
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={toggleOpen}
            className="relative flex items-center justify-center rounded-full border border-envoi-border bg-envoi-bg p-2.5 shadow-sm transition-colors hover:bg-envoi-surface"
          >
            <MessageSquare size={18} className="text-envoi-text-muted" />
            {hasUnread && (
              <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-envoi-accent" />
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="left">Open agent chat</TooltipContent>
      </Tooltip>
    </div>
  );
}
