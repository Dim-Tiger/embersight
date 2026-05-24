"use client";

import { useTheme } from "@/lib/theme";
import { useEffect } from "react";

export function ThemeApplier() {
  const theme = useTheme((s) => s.theme);
  const hydrate = useTheme((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.classList.toggle("light", theme === "light");
    root.style.colorScheme = theme;

    // Sync the 32×32 favicon with the in-app theme so the browser tab
    // icon matches even when the OS theme differs from the user's choice.
    const favicon32 = document.querySelector(
      'link[sizes="32x32"]',
    ) as HTMLLinkElement | null;
    if (favicon32) {
      favicon32.href =
        theme === "dark"
          ? "/brand/favicon-32-dark.png"
          : "/brand/favicon-32.png";
    }
  }, [theme]);

  return null;
}
