'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { getLocalAccessToken, isLocalDevAuthBypass } from '@/lib/api';

export default function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [authorized, setAuthorized] = useState(false);

  useEffect(() => {
    if (isLocalDevAuthBypass()) {
      setAuthorized(true);
      return;
    }
    if (!getLocalAccessToken()) {
      router.replace('/login');
      return;
    }
    setAuthorized(true);
  }, [router]);

  if (!authorized) {
    return (
      <main className="flex h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        Checking sign-in…
      </main>
    );
  }

  return <>{children}</>;
}
