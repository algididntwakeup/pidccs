'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, LockKeyhole, ShieldCheck } from 'lucide-react';
import {
  exchangeBimaSsoToken,
  exchangeMockBimaIdentity,
  isLocalDevAuthBypass,
  setLocalAccessToken,
} from '@/lib/api';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const BIMA_BASE_URL = process.env.NEXT_PUBLIC_BIMA_BASE_URL?.trim().replace(/\/+$/, '') || '';
const BIMA_CLIENT_ID = process.env.NEXT_PUBLIC_BIMA_CLIENT_ID?.trim() || '';
const BIMA_REDIRECT_URI = process.env.NEXT_PUBLIC_BIMA_REDIRECT_URI?.trim() || '';
const SSO_STATE_KEY = 'pid_bima_sso_state';

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [mockEmail, setMockEmail] = useState('');
  const [mockName, setMockName] = useState('');

  useEffect(() => {
    if (isLocalDevAuthBypass()) {
      router.replace('/');
      return;
    }
    const callbackUrl = new URL(window.location.href);
    const token = callbackUrl.searchParams.get('token');
    const returnedState = callbackUrl.searchParams.get('state');
    const bimaError = callbackUrl.searchParams.get('error_description') || callbackUrl.searchParams.get('error');

    if (bimaError) {
      setError(`BIMA sign-in failed: ${bimaError}`);
      window.history.replaceState({}, '', callbackUrl.pathname);
      return;
    }
    if (!token) return;

    // Remove the upstream bearer token from the visible URL/history before any API work.
    window.history.replaceState({}, '', callbackUrl.pathname);
    const expectedState = window.sessionStorage.getItem(SSO_STATE_KEY);
    window.sessionStorage.removeItem(SSO_STATE_KEY);
    if (!returnedState || !expectedState || returnedState !== expectedState) {
      setError('BIMA sign-in state is invalid or expired. Please start again.');
      return;
    }

    let cancelled = false;
    setLoading(true);
    exchangeBimaSsoToken(token)
      .then((response) => {
        if (cancelled) return;
        setLocalAccessToken(response.access_token);
        router.replace('/');
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'BIMA sign-in failed');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [router]);

  function startBimaLogin() {
    setError('');
    if (!BIMA_BASE_URL || !BIMA_CLIENT_ID) {
      setError('NEXT_PUBLIC_BIMA_BASE_URL dan NEXT_PUBLIC_BIMA_CLIENT_ID harus diisi untuk mengaktifkan BIMA SSO.');
      return;
    }

    const state = window.crypto.randomUUID();
    window.sessionStorage.setItem(SSO_STATE_KEY, state);
    const redirectUri = BIMA_REDIRECT_URI || `${window.location.origin}/login`;
    const authorizeUrl = new URL('/sso/authorize', BIMA_BASE_URL);
    authorizeUrl.searchParams.set('client_id', BIMA_CLIENT_ID);
    authorizeUrl.searchParams.set('redirect_uri', redirectUri);
    authorizeUrl.searchParams.set('state', state);
    authorizeUrl.searchParams.set('scope', 'profile roles permissions email department');
    window.location.assign(authorizeUrl.toString());
  }

  async function startMockLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!mockEmail.trim() || !mockName.trim()) return;
    setError('');
    setLoading(true);
    try {
      const response = await exchangeMockBimaIdentity({
        bima_user_id: `mock-${mockEmail.trim().toLowerCase()}`,
        email: mockEmail.trim(),
        full_name: mockName.trim(),
      });
      setLocalAccessToken(response.access_token);
      router.replace('/');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Mock sign-in failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4 py-10">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary text-xl font-bold text-primary-foreground shadow-sm">
            P
          </div>
          <h1 className="mt-4 text-xl font-bold tracking-tight text-foreground">
            P&amp;ID Studio Web Platform
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            API RP 970 Corrosion Control Document Digitization
          </p>
        </div>

        <Card className="shadow-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <LockKeyhole className="h-4 w-4 text-primary" />
              Masuk ke Workspace
            </CardTitle>
            <CardDescription>
              Autentikasi dilakukan melalui portal BIMA. P&amp;ID Studio hanya menerima token
              sesi lokal setelah backend memverifikasi identitas Anda.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex gap-3 rounded-md border border-border bg-secondary/60 p-3 text-xs leading-5 text-secondary-foreground">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <p>
                Sesi terenkripsi end-to-end. Anda akan diarahkan ke portal resmi BIMA untuk
                autentikasi dan kembali secara otomatis.
              </p>
            </div>

            {error && (
              <p
                role="alert"
                className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
              >
                {error}
              </p>
            )}

            <Button
              type="button"
              onClick={startBimaLogin}
              disabled={loading}
              className="w-full"
              size="lg"
            >
              <LockKeyhole className="h-4 w-4" />
              <span>{loading ? 'Memverifikasi sesi BIMA…' : 'Login dengan BIMA'}</span>
              {!loading && <ArrowRight className="h-4 w-4" />}
            </Button>

            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t border-border" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-card px-2 font-medium text-muted-foreground">
                  Akses Pengembang
                </span>
              </div>
            </div>

            <form onSubmit={startMockLogin} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="mock-name">Nama Lengkap</Label>
                <Input
                  id="mock-name"
                  value={mockName}
                  onChange={(e) => setMockName(e.target.value)}
                  placeholder="Contoh: Engineer Reksolindo"
                  autoComplete="name"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mock-email">Email Perusahaan</Label>
                <Input
                  id="mock-email"
                  type="email"
                  value={mockEmail}
                  onChange={(e) => setMockEmail(e.target.value)}
                  placeholder="nama@reksolindo.co.id"
                  autoComplete="email"
                />
              </div>
              <Button
                type="submit"
                variant="outline"
                className="w-full"
                disabled={loading || !mockEmail.trim() || !mockName.trim()}
              >
                Masuk sebagai Engineer (Mock)
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="text-center text-xs text-muted-foreground">
          © {new Date().getFullYear()} PT Rekayasa Solusi Indonesia · PIDCCS
        </p>
      </div>
    </main>
  );
}
