import "./globals.css";
import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { Providers } from "@/app/providers";

export const metadata: Metadata = {
  title: "ReForge",
  description: "AI-powered content automation SaaS"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}

