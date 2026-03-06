/**
 * Chat drawer — right-side slide-over panel.
 * Always mounted, animated with react-spring.
 *
 * Layout: section with flex-col, overflow-hidden.
 *   header (shrink-0)
 *   messages (flex-1 overflow-y-auto) ← takes all remaining space
 *   input (shrink-0) ← pinned to bottom
 *
 * The animated wrapper uses overflow-x:clip (not overflow:hidden)
 * so height stretches naturally in the parent flex row.
 */

"use client";

import { useSpring, animated } from "@react-spring/web";
import { useChatContext } from "./chat-provider";
import { ChatHeader } from "./chat-header";
import { ChatMessages } from "./chat-messages";
import { ChatInput } from "./chat-input";

const DRAWER_WIDTH = 440;

/** Slide-over chat panel anchored to the right edge */
export function ChatDrawer() {
  const { isOpen } = useChatContext();

  const spring = useSpring({
    width: isOpen ? DRAWER_WIDTH : 0,
    opacity: isOpen ? 1 : 0,
    config: { tension: 300, friction: 30 },
  });

  return (
    <animated.div
      style={{
        width: spring.width,
        opacity: spring.opacity,
        minWidth: 0,
      }}
      className="shrink-0 self-stretch overflow-x-clip border-l border-envoi-border bg-envoi-bg"
    >
      <section
        className="flex h-full flex-col overflow-hidden"
        style={{ width: DRAWER_WIDTH, minWidth: DRAWER_WIDTH }}
      >
        <ChatHeader />
        <ChatMessages />
        <ChatInput />
      </section>
    </animated.div>
  );
}
