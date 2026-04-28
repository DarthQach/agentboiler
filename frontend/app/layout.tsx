import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentBoiler Approvals",
  description: "Operator approval queue for AgentBoiler tool calls.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
