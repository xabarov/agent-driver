import { useState, type ReactNode } from "react";
import { Menu, X } from "lucide-react";

import { Button } from "../ui/button";

interface AppShellProps {
  header: ReactNode;
  sidebar: ReactNode;
  children: ReactNode;
}

export function AppShell({ header, sidebar, children }: AppShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="min-h-screen px-4 py-4">
      <div className="mx-auto max-w-7xl">
        <div className="mb-3 flex items-center justify-between lg:hidden">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            onClick={() => setMobileOpen((value) => !value)}
          >
            {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>
          <span className="text-sm font-medium">Sessions</span>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_1fr]">
          <aside
            className={`lg:sticky lg:top-4 lg:block ${mobileOpen ? "block" : "hidden"}`}
          >
            {sidebar}
          </aside>
          <div className="space-y-4">
            {header}
            <main className="mx-auto w-full max-w-3xl">{children}</main>
          </div>
        </div>
      </div>
    </div>
  );
}
