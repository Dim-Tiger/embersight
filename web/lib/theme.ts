"use client";

import { create } from "zustand";

export type Theme = "dark" | "light";

type ThemeStore = {
  theme: Theme;
  hydrated: boolean;
  setTheme: (t: Theme) => void;
  toggle: () => void;
  hydrate: () => void;
};

const STORAGE_KEY = "embersight-theme";

function readInitial(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "dark" || stored === "light") return stored;
  const prefersLight = window.matchMedia?.("(prefers-color-scheme: light)").matches;
  return prefersLight ? "light" : "dark";
}

export const useTheme = create<ThemeStore>((set, get) => ({
  theme: "dark",
  hydrated: false,
  setTheme: (t) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, t);
    }
    set({ theme: t });
  },
  toggle: () => {
    const next = get().theme === "dark" ? "light" : "dark";
    get().setTheme(next);
  },
  hydrate: () => {
    if (get().hydrated) return;
    set({ theme: readInitial(), hydrated: true });
  },
}));
