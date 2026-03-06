/**
 * Chat drawer — right-side slide-over panel.
 * Always mounted, animated with react-spring.
 *
 * Behaves like the sidebar: in layout when open, zero width when closed.
 * The fixed-width panel is absolutely positioned inside the animated rail so
 * it never reserves layout width while collapsed.
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
        minWidth: spring.width,
        flexBasis: spring.width,
        opacity: spring.opacity,
      }}
      className="relative shrink-0 self-stretch overflow-hidden"
    >
      <section
        className="absolute inset-y-0 right-0 flex h-full w-[440px] min-w-[440px] flex-col overflow-hidden border-l border-envoi-border bg-envoi-bg"
        style={{ pointerEvents: isOpen ? "auto" : "none" }}
      >
        <ChatHeader />
        <ChatMessages />
        <ChatInput />
      </section>
    </animated.div>
  );
}
