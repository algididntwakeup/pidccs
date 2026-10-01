'use client';

import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import Link from 'next/link';
import {
  AdminMonitoringSnapshot,
  exchangeBimaSsoToken,
  exchangeSudoSecret,
  fetchAdminMonitoring,
  getLocalAccessToken,
  setLocalAccessToken,
} from '@/lib/api';

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

export default function SudoMonitoringPage() {
  const [loginMode, setLoginMode] = useState<'bima' | 'breakglass'>('bima');
  const [bimaToken, setBimaToken] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [snapshot, setSnapshot] = useState<AdminMonitoringSnapshot | null>(null);
  const [adminName, setAdminName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadSnapshot = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setSnapshot(await fetchAdminMonitoring());
    } catch (err) {
      setLocalAccessToken(null);
      setSnapshot(null);
      setError(err instanceof Error ? err.message : 'Admin access could not be verified');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (getLocalAccessToken()) void loadSnapshot();
  }, [loadSnapshot]);

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const session = await exchangeBimaSsoToken(bimaToken.trim());
      if (session.user.role.toUpperCase() !== 'SUPERADMIN') {
        throw new Error('Akun BIMA ini tidak memiliki role SUPERADMIN.');
      }
      setLocalAccessToken(session.access_token);
      setAdminName(session.user.full_name || session.user.email);
      setBimaToken('');
      setSnapshot(await fetchAdminMonitoring());
    } catch (err) {
      setLocalAccessToken(null);
      setError(err instanceof Error ? err.message : 'BIMA SSO login failed');
    } finally {
      setLoading(false);
    }
  }

  async function handleBreakGlassLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError('');
    const submittedSecret = secretKey;
    setSecretKey('');
    try {
      const session = await exchangeSudoSecret(submittedSecret);
      setLocalAccessToken(session.access_token);
      setAdminName('Emergency IT Superuser');
      setSnapshot(await fetchAdminMonitoring());
    } catch (err) {
      setLocalAccessToken(null);
      setError(err instanceof Error ? err.message : 'Break-glass login failed');
    } finally {
      setLoading(false);
    }
  }

  function signOut() {
    setLocalAccessToken(null);
    setSnapshot(null);
    setAdminName('');
  }

  return (
    <main className="h-screen overflow-auto bg-slate-950 px-5 py-8 text-slate-100 md:px-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-400">Restricted · BIMA SSO</p>
            <h1 className="mt-2 text-3xl font-semibold">IT Monitoring Dashboard</h1>
            <p className="mt-1 text-sm text-slate-400">Project workspace, marking activity, and processing errors</p>
          </div>
          {snapshot && (
            <div className="flex items-center gap-3">
              <span className="text-sm text-slate-300">{adminName || 'SUPERADMIN'}</span>
              <button onClick={signOut} className="rounded-lg border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">Sign out</button>
            </div>
          )}
        </header>

        {!snapshot ? (
          <section className="mx-auto mt-16 max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl">
            <h2 className="text-xl font-semibold">Authenticate with BIMA</h2>
            <div className="mt-5 grid grid-cols-2 gap-2 rounded-lg bg-slate-950 p-1">
              <button type="button" onClick={() => { setLoginMode('bima'); setError(''); }} className={`rounded-md px-3 py-2 text-sm ${loginMode === 'bima' ? 'bg-slate-800 text-white' : 'text-slate-400'}`}>BIMA SSO</button>
              <button type="button" onClick={() => { setLoginMode('breakglass'); setError(''); }} className={`rounded-md px-3 py-2 text-sm ${loginMode === 'breakglass' ? 'bg-amber-950 text-amber-200' : 'text-slate-400'}`}>Emergency IT</button>
            </div>
            {loginMode === 'bima' ? (
              <>
                <p className="mt-3 text-sm leading-6 text-slate-400">Use a valid BIMA SSO access token. Admin access is granted when BIMA verifies the SUPERADMIN role.</p>
                <form onSubmit={handleLogin} className="mt-5 space-y-4">
                  <label htmlFor="bima-token" className="block text-sm font-medium text-slate-200">BIMA SSO token</label>
                  <input id="bima-token" type="password" autoComplete="off" required value={bimaToken} onChange={(event) => setBimaToken(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-sm outline-none focus:border-cyan-500" placeholder="Paste your BIMA SSO token" />
                  {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
                  <button disabled={loading} className="w-full rounded-lg bg-cyan-600 px-4 py-3 font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">{loading ? 'Verifying with BIMA…' : 'Continue with BIMA SSO'}</button>
                </form>
              </>
            ) : (
              <>
                <p className="mt-3 text-sm leading-6 text-amber-200/80">Emergency access for IT when BIMA role synchronization is unavailable. Attempts are recorded; this method is disabled unless configured by deployment.</p>
                <form onSubmit={handleBreakGlassLogin} className="mt-5 space-y-4">
                  <label htmlFor="sudo-secret" className="block text-sm font-medium text-slate-200">Break-glass secret key</label>
                  <input id="sudo-secret" type="password" autoComplete="new-password" required value={secretKey} onChange={(event) => setSecretKey(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-sm outline-none focus:border-amber-500" placeholder="Deployment secret" />
                  {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
                  <button disabled={loading} className="w-full rounded-lg bg-amber-700 px-4 py-3 font-semibold text-white hover:bg-amber-600 disabled:opacity-50">{loading ? 'Verifying…' : 'Enter emergency dashboard'}</button>
                </form>
              </>
            )}
          </section>
        ) : (
          <>
            {error && <p role="alert" className="mb-5 rounded-lg border border-rose-900 bg-rose-950/50 p-3 text-sm text-rose-200">{error}</p>}
            <section className="mb-8 grid gap-4 sm:grid-cols-3">
              <Metric label="Projects" value={snapshot.projects.length} />
              <Metric label="Recent marking events" value={snapshot.activities.length} />
              <Metric label="Recent processing errors" value={snapshot.recent_errors.length} />
            </section>

            <section className="mb-8 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
              <div className="border-b border-slate-800 px-5 py-4"><h2 className="font-semibold">All projects</h2></div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead className="bg-slate-950/70 text-xs uppercase tracking-wide text-slate-400"><tr><th className="px-5 py-3">Project</th><th className="px-5 py-3">BIMA ID</th><th className="px-5 py-3">Owner</th><th className="px-5 py-3">Sheets</th><th className="px-5 py-3">Workspace</th></tr></thead>
                  <tbody className="divide-y divide-slate-800">
                    {snapshot.projects.map((project) => (
                      <tr key={project.id}>
                        <td className="px-5 py-4 font-medium">{project.name}</td>
                        <td className="px-5 py-4 text-slate-400">{project.bima_project_id || '—'}</td>
                        <td className="px-5 py-4">{project.owner_name || <span className="text-slate-500">BIMA assigned</span>}<div className="text-xs text-slate-500">{project.owner_email || ''}</div></td>
                        <td className="px-5 py-4 text-slate-300">{project.sheets.length}</td>
                        <td className="px-5 py-4">
                          {project.sheets.length ? (
                            <div className="flex flex-wrap gap-2">
                              {project.sheets.map((sheet) => (
                                <Link key={sheet.id} href={`/project/${project.id}/sheet/${sheet.id}`} className="rounded-md border border-cyan-800 px-2.5 py-1.5 text-xs text-cyan-300 hover:bg-cyan-950">
                                  Open {sheet.sheet_number || sheet.filename}
                                </Link>
                              ))}
                            </div>
                          ) : <span className="text-slate-500">No sheets</span>}
                        </td>
                      </tr>
                    ))}
                    {!snapshot.projects.length && <tr><td colSpan={5} className="px-5 py-8 text-center text-slate-500">No projects found.</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="mb-8 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
              <div className="border-b border-slate-800 px-5 py-4"><h2 className="font-semibold">Ground-truth activity</h2></div>
              <div className="max-h-[420px] overflow-auto">
                <table className="w-full min-w-[800px] text-left text-sm">
                  <thead className="sticky top-0 bg-slate-950 text-xs uppercase tracking-wide text-slate-400"><tr><th className="px-5 py-3">When</th><th className="px-5 py-3">User</th><th className="px-5 py-3">Action</th><th className="px-5 py-3">Project / sheet</th><th className="px-5 py-3">Payload</th></tr></thead>
                  <tbody className="divide-y divide-slate-800">
                    {snapshot.activities.map((activity) => (
                      <tr key={activity.id}>
                        <td className="whitespace-nowrap px-5 py-3 text-slate-400">{formatDate(activity.timestamp)}</td>
                        <td className="px-5 py-3">{activity.user_name}<div className="text-xs text-slate-500">{activity.user_email}</div></td>
                        <td className="px-5 py-3 font-mono text-cyan-300">{activity.action_type}</td>
                        <td className="px-5 py-3">{activity.project_name}<div className="text-xs text-slate-500">{activity.sheet_name || 'Project level'}</div></td>
                        <td className="max-w-[300px] truncate px-5 py-3 font-mono text-xs text-slate-400" title={JSON.stringify(activity.payload_json)}>{JSON.stringify(activity.payload_json)}</td>
                      </tr>
                    ))}
                    {!snapshot.activities.length && <tr><td colSpan={5} className="px-5 py-8 text-center text-slate-500">No marking activity recorded.</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
              <div className="border-b border-slate-800 px-5 py-4"><h2 className="font-semibold">Recent processing errors</h2></div>
              <div className="max-h-[360px] overflow-auto divide-y divide-slate-800">
                {snapshot.recent_errors.map((job) => (
                  <article key={job.id} className="px-5 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{job.project_name} · {job.sheet_name}</span><time className="text-xs text-slate-500">{formatDate(job.created_at)}</time></div>
                    <p className="mt-1 text-xs uppercase text-rose-300">{job.status} · {job.message}</p>
                    <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-xs text-slate-400">{job.error}</pre>
                  </article>
                ))}
                {!snapshot.recent_errors.length && <p className="px-5 py-8 text-center text-slate-500">No recent processing errors.</p>}
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="rounded-xl border border-slate-800 bg-slate-900 p-5"><p className="text-sm text-slate-400">{label}</p><p className="mt-2 text-3xl font-semibold">{value}</p></div>;
}
