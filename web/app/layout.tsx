import "./globals.css";
import type { Metadata } from "next";
import { ThemeApplier } from "./components/ThemeApplier";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "EmberSight",
  description:
    "Human-centered AI for CAL FIRE Incident Management Teams. EmberSight never dispatches.",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      // System-theme-aware PNG favicons (browsers that honor `media` swap).
      {
        url: "/brand/favicon-32.png",
        type: "image/png",
        sizes: "32x32",
        media: "(prefers-color-scheme: light)",
      },
      {
        url: "/brand/favicon-32-dark.png",
        type: "image/png",
        sizes: "32x32",
        media: "(prefers-color-scheme: dark)",
      },
      { url: "/brand/icon-192.png", type: "image/png", sizes: "192x192" },
      { url: "/brand/icon-512.png", type: "image/png", sizes: "512x512" },
    ],
    apple: "/brand/apple-touch-icon.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Default to dark; ThemeApplier hydrates from localStorage / system on mount.
  return (
    <html lang="en" className="dark" style={{ colorScheme: "dark" }}>
      <body className="min-h-screen bg-smoke-900 text-smoke-200 antialiased">
        <ThemeApplier />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
