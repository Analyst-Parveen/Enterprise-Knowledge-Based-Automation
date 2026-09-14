import type { Metadata } from "next";

import { AppShell } from "@/components/shell";

import "./globals.css";

export const metadata: Metadata = {
  title: "Enterprise Knowledge AI",
  description: "Private, tenant-aware enterprise knowledge assistant.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
