'use client';

import type { ReactNode } from 'react';
import RequireAuth from '@/components/RequireAuth';

export default function ProjectLayout({ children }: { children: ReactNode }) {
  return <RequireAuth>{children}</RequireAuth>;
}
