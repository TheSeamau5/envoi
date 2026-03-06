/**
 * Chat drawer header — uniform h-10.25 to match sidebar and compare shell headers.
 * Title left, action buttons right.
 */

"use client";

import { X, Trash2 } from "lucide-react";
import { useChatContext } from "./chat-provider";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** Header bar for the chat drawer — matches global h-10.25 header height */
export function ChatHeader() {
  const { toggleOpen, clearConversation, messages } = useChatContext();
  const hasMessages = messages.length > 0;

  return (
    <div className="flex h-10.25 shrink-0 items-center justify-between border-b border-envoi-border px-3">
      <span className="text-[11px] font-bold uppercase tracking-[0.08em] text-envoi-text-muted">
        Agent
      </span>
      <div className="flex items-center gap-0.5">
        {hasMessages && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={clearConversation}
                className="rounded p-1.5 text-envoi-text-dim transition-colors hover:bg-envoi-surface hover:text-envoi-text-muted"
              >
                <Trash2 size={13} />
              </button>
            </TooltipTrigger>
            <TooltipContent>Clear conversation</TooltipContent>
          </Tooltip>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={toggleOpen}
              className="rounded p-1.5 text-envoi-text-dim transition-colors hover:bg-envoi-surface hover:text-envoi-text-muted"
            >
              <X size={13} />
            </button>
          </TooltipTrigger>
          <TooltipContent>Close</TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}
