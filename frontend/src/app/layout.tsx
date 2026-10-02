import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-plus-jakarta",
});

export const metadata: Metadata = {
  title: "P&ID Studio Web Platform",
  description:
    "Web-Based Re-Engineering of P&ID Digitization & API RP 970 Corrosion Systemization",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`h-full ${plusJakartaSans.variable}`}>
      <body className="h-full bg-surface font-sans antialiased">{children}</body>
    </html>
  );
}
