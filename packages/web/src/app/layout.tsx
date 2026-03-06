/**
 * Root layout — server component.
 * Loads monospace font and provides the app shell with sidebar.
 */

import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Providers } from "@/components/providers";
import { readLayoutCookies } from "@/lib/cookies";

const monoFont = localFont({
  src: [
    { path: "../fonts/DejaVuSansMono.ttf", weight: "400", style: "normal" },
    {
      path: "../fonts/DejaVuSansMono-Bold.ttf",
      weight: "700",
      style: "normal",
    },
  ],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "envoi",
  description: "Dashboard for evaluating AI coding agents",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const { chatHasMessages } = await readLayoutCookies();

  return (
    <html lang="en" className={monoFont.variable}>
      <body className="font-mono antialiased">
        <Providers initialChatHasMessages={chatHasMessages}>
          <div className="flex h-screen overflow-hidden">{children}</div>
        </Providers>
      </body>
    </html>
  );
}
