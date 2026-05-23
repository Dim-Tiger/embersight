import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "EmberSight",
  description:
    "Human-centered AI for CAL FIRE Incident Management Teams. EmberSight never dispatches.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-smoke-900 text-smoke-200 antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
