import { useState, type ReactNode } from "react";
import { Menu, X } from "lucide-react";

import { cn } from "../../lib/cn";
import { Button } from "../ui/button";

interface AppShellProps {
  header: ReactNode;
  sidebar: ReactNode;
  children: ReactNode;
}

export function AppShell({ header, sidebar, children }: AppShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            "flex w-[var(--sidebar-width)] shrink-0 flex-col border-r border-border bg-card/30",
            "lg:relative lg:translate-x-0",
            mobileOpen
              ? "fixed inset-y-0 left-0 z-40 translate-x-0 shadow-xl"
              : "fixed inset-y-0 left-0 z-40 -translate-x-full lg:translate-x-0",
          )}
        >
          {sidebar}
        </aside>
        {mobileOpen ? (
          <button
            type="button"
            className="fixed inset-0 z-30 bg-black/50 lg:hidden"
            aria-label="Close sidebar"
            onClick={() => setMobileOpen(false)}
          />
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="shrink-0 border-b border-border px-3 py-2 lg:px-4">
            <div className="flex min-w-0 items-start gap-2">
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-9 w-9 shrink-0 lg:hidden"
                onClick={() => setMobileOpen((value) => !value)}
                aria-label={mobileOpen ? "Close sidebar" : "Open sidebar"}
              >
                {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
              </Button>
              <div className="min-w-0 flex-1">{header}</div>
            </div>
          </header>
          <main className="flex min-h-0 flex-1 flex-col">{children}</main>
        </div>
      </div>
    </div>
  );
}
