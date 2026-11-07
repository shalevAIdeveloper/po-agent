import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PO Agent",
  description: "Turn purchase orders into validated, ERP-ready sales orders.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // The theme class (`.dark`) goes on <html> and is set before paint (ADR 0002),
    // so React must not warn when it differs from the server render.
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
