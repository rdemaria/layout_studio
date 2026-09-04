import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Layout Studio",
  description: "Edit curve-referenced layouts and inspect their geometry in 3D.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
  other: {
    "codex-preview": "development",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
