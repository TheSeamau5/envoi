/**
 * Root layout — server component.
 * Loads monospace font and provides the app shell with sidebar.
 */

import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Providers } from "@/components/providers";
import { Sidebar } from "@/components/sidebar";
import { BottomBar } from "@/components/bottom-bar";

const monoFont = localFont({
  src: [
    { path: "../fonts/DejaVuSansMono.ttf", weight: "400", style: "normal" },
    { path: "../fonts/DejaVuSansMono-Bold.ttf", weight: "700", style: "normal" },
  ],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "envoi",
  description: "Dashboard for evaluating AI coding agents",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={monoFont.variable}>
      <body className="font-mono antialiased">
        <Providers>
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <div className="flex flex-1 flex-col overflow-hidden">
              {children}
              <BottomBar />
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
