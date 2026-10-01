'use client';

import { useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, LockKeyhole, ShieldCheck } from 'lucide-react';
import { exchangeMockBimaIdentity, setLocalAccessToken } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [bimaUserId, setBimaUserId] = useState('ENG-001');
  const [email, setEmail] = useState('algi@reksolindo.co.id');
  const [fullName, setFullName] = useState('Algi Engineer');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const response = await exchangeMockBimaIdentity({
        bima_user_id: bimaUserId.trim(),
        email: email.trim(),
        full_name: fullName.trim(),
      });
      setLocalAccessToken(response.access_token);
      router.replace('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'BIMA sign-in failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4 py-10 text-slate-100">
      <section className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-7 shadow-2xl">
        <div className="mb-7 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-600 text-xl font-bold">P</div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-300">BIMA SSO · Development</p>
            <h1 className="mt-1 text-xl font-semibold">Masuk ke P&amp;ID Studio</h1>
          </div>
        </div>

        <div className="mb-6 flex gap-3 rounded-xl border border-amber-900/70 bg-amber-950/40 p-3 text-xs leading-5 text-amber-100">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
          <p>Simulasi ini mengirim identitas tester ke callback SSO backend. Aktifkan `BIMA_SSO_MOCK_MODE=true` hanya di environment development.</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="BIMA User ID" value={bimaUserId} onChange={setBimaUserId} placeholder="ENG-001" />
          <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="nama@reksolindo.co.id" />
          <Field label="Nama lengkap" value={fullName} onChange={setFullName} placeholder="Nama engineer" />

          {error && <p role="alert" className="rounded-lg border border-rose-900 bg-rose-950/50 p-3 text-sm text-rose-200">{error}</p>}

          <button
            type="submit"
            disabled={loading || !bimaUserId.trim() || !email.trim() || !fullName.trim()}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-3 font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <LockKeyhole className="h-4 w-4" />
            <span>{loading ? 'Menghubungkan ke BIMA…' : 'Simulate Login via BIMA'}</span>
            {!loading && <ArrowRight className="h-4 w-4" />}
          </button>
        </form>

        <p className="mt-5 text-center text-xs text-slate-500">Mock login hanya tersedia ketika backend mengaktifkan mode SSO development.</p>
      </section>
    </main>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  type?: string;
}) {
  return (
    <label className="block space-y-1.5 text-sm font-medium text-slate-200">
      <span>{label}</span>
      <input
        type={type}
        required
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm outline-none transition placeholder:text-slate-600 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
      />
    </label>
  );
}
