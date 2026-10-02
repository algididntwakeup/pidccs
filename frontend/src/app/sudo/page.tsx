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
  isLocalDevAuthBypass,
  setLocalAccessToken,
} from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';

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
    if (isLocalDevAuthBypass() || getLocalAccessToken()) void loadSnapshot();
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
    <main className="h-screen overflow-auto bg-surface px-5 py-8 text-foreground md:px-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4 border-b border-border pb-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-primary">
              {isLocalDevAuthBypass() ? 'Local Dev Mode' : 'Restricted · BIMA SSO'}
            </p>
            <h1 className="mt-2 text-3xl font-semibold">IT Monitoring Dashboard</h1>
            <p className="mt-1 text-sm text-muted-foreground">Project workspace, marking activity, and processing errors</p>
          </div>
          {snapshot && !isLocalDevAuthBypass() && (
            <div className="flex items-center gap-3">
              <span className="text-sm text-muted-foreground">{adminName || 'SUPERADMIN'}</span>
              <Button variant="outline" size="sm" onClick={signOut}>Sign out</Button>
            </div>
          )}
        </header>

        {!snapshot ? (
          <Card className="mx-auto mt-16 max-w-md shadow-lg">
            <CardHeader>
              <CardTitle className="text-xl">Authenticate with BIMA</CardTitle>
            </CardHeader>
            <CardContent>
            <div className="grid grid-cols-2 gap-2 rounded-lg bg-muted p-1">
              <button type="button" onClick={() => { setLoginMode('bima'); setError(''); }} className={`rounded-md px-3 py-2 text-sm font-medium transition ${loginMode === 'bima' ? 'bg-card text-primary shadow-sm' : 'text-muted-foreground'}`}>BIMA SSO</button>
              <button type="button" onClick={() => { setLoginMode('breakglass'); setError(''); }} className={`rounded-md px-3 py-2 text-sm font-medium transition ${loginMode === 'breakglass' ? 'bg-card text-amber-700 shadow-sm' : 'text-muted-foreground'}`}>Emergency IT</button>
            </div>
            {loginMode === 'bima' ? (
              <>
                <p className="mt-3 text-sm leading-6 text-muted-foreground">Use a valid BIMA SSO access token. Admin access is granted when BIMA verifies the SUPERADMIN role.</p>
                <form onSubmit={handleLogin} className="mt-5 space-y-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="bima-token">BIMA SSO token</Label>
                    <Input id="bima-token" type="password" autoComplete="off" required value={bimaToken} onChange={(event) => setBimaToken(event.target.value)} className="font-mono" placeholder="Paste your BIMA SSO token" />
                  </div>
                  {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                  <Button type="submit" disabled={loading} className="w-full" size="lg">{loading ? 'Verifying with BIMA…' : 'Continue with BIMA SSO'}</Button>
                </form>
              </>
            ) : (
              <>
                <p className="mt-3 text-sm leading-6 text-amber-700">Emergency access for IT when BIMA role synchronization is unavailable. Attempts are recorded; this method is disabled unless configured by deployment.</p>
                <form onSubmit={handleBreakGlassLogin} className="mt-5 space-y-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="sudo-secret">Break-glass secret key</Label>
                    <Input id="sudo-secret" type="password" autoComplete="new-password" required value={secretKey} onChange={(event) => setSecretKey(event.target.value)} className="font-mono" placeholder="Deployment secret" />
                  </div>
                  {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                  <Button type="submit" disabled={loading} size="lg" className="w-full bg-amber-600 text-white hover:bg-amber-700">{loading ? 'Verifying…' : 'Enter emergency dashboard'}</Button>
                </form>
              </>
            )}
            </CardContent>
          </Card>
        ) : (
          <>
            {error && <p role="alert" className="mb-5 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
            <section className="mb-8 grid gap-4 sm:grid-cols-3">
              <Metric label="Projects" value={snapshot.projects.length} />
              <Metric label="Recent marking events" value={snapshot.activities.length} />
              <Metric label="Recent processing errors" value={snapshot.recent_errors.length} />
            </section>

            <Card className="mb-8 overflow-hidden">
              <CardHeader className="border-b border-border py-4"><CardTitle className="text-base">All projects</CardTitle></CardHeader>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead className="bg-muted/60 text-xs uppercase tracking-wide text-muted-foreground"><tr><th className="px-5 py-3">Project</th><th className="px-5 py-3">BIMA ID</th><th className="px-5 py-3">Owner</th><th className="px-5 py-3">Sheets</th><th className="px-5 py-3">Workspace</th></tr></thead>
                  <tbody className="divide-y divide-border">
                    {snapshot.projects.map((project) => (
                      <tr key={project.id}>
                        <td className="px-5 py-4 font-medium">{project.name}</td>
                        <td className="px-5 py-4 text-muted-foreground">{project.bima_project_id || '—'}</td>
                        <td className="px-5 py-4">{project.owner_name || <span className="text-muted-foreground">BIMA assigned</span>}<div className="text-xs text-muted-foreground">{project.owner_email || ''}</div></td>
                        <td className="px-5 py-4 text-muted-foreground">{project.sheets.length}</td>
                        <td className="px-5 py-4">
                          {project.sheets.length ? (
                            <div className="flex flex-wrap gap-2">
                              {project.sheets.map((sheet) => (
                                <Link key={sheet.id} href={`/project/${project.id}/sheet/${sheet.id}`} className="rounded-md border border-border px-2.5 py-1.5 text-xs text-primary hover:bg-accent">
                                  Open {sheet.sheet_number || sheet.filename}
                                </Link>
                              ))}
                            </div>
                          ) : <span className="text-muted-foreground">No sheets</span>}
                        </td>
                      </tr>
                    ))}
                    {!snapshot.projects.length && <tr><td colSpan={5} className="px-5 py-8 text-center text-muted-foreground">No projects found.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card className="mb-8 overflow-hidden">
              <CardHeader className="border-b border-border py-4"><CardTitle className="text-base">Ground-truth activity</CardTitle></CardHeader>
              <div className="max-h-[420px] overflow-auto">
                <table className="w-full min-w-[800px] text-left text-sm">
                  <thead className="sticky top-0 bg-muted text-xs uppercase tracking-wide text-muted-foreground"><tr><th className="px-5 py-3">When</th><th className="px-5 py-3">User</th><th className="px-5 py-3">Action</th><th className="px-5 py-3">Project / sheet</th><th className="px-5 py-3">Payload</th></tr></thead>
                  <tbody className="divide-y divide-border">
                    {snapshot.activities.map((activity) => (
                      <tr key={activity.id}>
                        <td className="whitespace-nowrap px-5 py-3 text-muted-foreground">{formatDate(activity.timestamp)}</td>
                        <td className="px-5 py-3">{activity.user_name}<div className="text-xs text-muted-foreground">{activity.user_email}</div></td>
                        <td className="px-5 py-3 font-mono text-primary">{activity.action_type}</td>
                        <td className="px-5 py-3">{activity.project_name}<div className="text-xs text-muted-foreground">{activity.sheet_name || 'Project level'}</div></td>
                        <td className="max-w-[300px] truncate px-5 py-3 font-mono text-xs text-muted-foreground" title={JSON.stringify(activity.payload_json)}>{JSON.stringify(activity.payload_json)}</td>
                      </tr>
                    ))}
                    {!snapshot.activities.length && <tr><td colSpan={5} className="px-5 py-8 text-center text-muted-foreground">No marking activity recorded.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card className="overflow-hidden">
              <CardHeader className="border-b border-border py-4"><CardTitle className="text-base">Recent processing errors</CardTitle></CardHeader>
              <div className="max-h-[360px] divide-y divide-border overflow-auto">
                {snapshot.recent_errors.map((job) => (
                  <article key={job.id} className="px-5 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{job.project_name} · {job.sheet_name}</span><time className="text-xs text-muted-foreground">{formatDate(job.created_at)}</time></div>
                    <p className="mt-1 text-xs uppercase text-destructive">{job.status} · {job.message}</p>
                    <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-muted p-3 text-xs text-muted-foreground">{job.error}</pre>
                  </article>
                ))}
                {!snapshot.recent_errors.length && <p className="px-5 py-8 text-center text-muted-foreground">No recent processing errors.</p>}
              </div>
            </Card>
          </>
        )}
      </div>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className="mt-2 text-3xl font-semibold">{value}</p>
      </CardContent>
    </Card>
  );
}
