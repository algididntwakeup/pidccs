import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "P&ID Studio Web Platform",
  description: "Web-Based Re-Engineering of P&ID Digitization & API RP 970 Corrosion Systemization",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full antialiased">{children}</body>
    </html>
  );
}
