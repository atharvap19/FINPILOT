"use client";

import { Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";

const KEY = "finpilot.theme";
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const isDark = () => document.documentElement.classList.contains("dark");

/** Light/dark switch. The class on <html> is the source of truth. */
export function ThemeToggle() {
  const dark = useSyncExternalStore(subscribe, isDark, () => true);

  const toggle = () => {
    const next = !isDark();
    document.documentElement.classList.toggle("dark", next);
    try {
      window.localStorage.setItem(KEY, next ? "dark" : "light");
    } catch {
      /* private mode: the choice just won't persist */
    }
    for (const listener of listeners) listener();
  };

  return (
    <Button size="icon" variant="ghost" onClick={toggle} title={dark ? "Switch to light mode" : "Switch to dark mode"}>
      {dark ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
      <span className="sr-only">{dark ? "Switch to light mode" : "Switch to dark mode"}</span>
    </Button>
  );
}
