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
  }, [theme]);

  return null;
}
