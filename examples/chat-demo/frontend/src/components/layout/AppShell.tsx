import type { ReactNode } from "react";

interface AppShellProps {
  header: ReactNode;
  children: ReactNode;
}

export function AppShell({ header, children }: AppShellProps) {
  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col px-4 py-4">
      {header}
      <main className="mt-4 flex-1">{children}</main>
    </div>
  );
}
