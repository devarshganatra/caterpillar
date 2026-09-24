import type { ReactNode } from 'react';
import { Sidebar } from './Sidebar';

/** Wraps authenticated views: fixed sidebar on the left, scrollable main content on the right. */
export function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <main className="flex-1 overflow-y-auto intel-scroll">
        {children}
      </main>
    </div>
  );
}
