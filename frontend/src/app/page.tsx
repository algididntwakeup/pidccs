'use client';

import { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  Plus,
  Folder,
  FolderPlus,
  Layers,
  Trash2,
  RefreshCw,
  Search,
  Clock,
  LayoutGrid,
  List as ListIcon,
  FolderOpen,
  AlertTriangle,
  LogOut,
} from 'lucide-react';
import { ProjectResponse } from '@/types/schema';
import {
  fetchProjects,
  createProject,
  deleteProject,
  isLocalDevAuthBypass,
  setLocalAccessToken,
} from '@/lib/api';
import RequireAuth from '@/components/RequireAuth';
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
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';

/**
 * Format timestamp to localized readable date & time (e.g. 28 Sep 2026, 14:30)
 */
function formatUploadTime(dateStr?: string) {
  if (!dateStr) return '-';
  try {
    const d = new Date(dateStr);
    return new Intl.DateTimeFormat('id-ID', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(d);
  } catch {
    return dateStr;
  }
}

export default function ProjectsPage() {
  return (
    <RequireAuth>
      <ProjectsDashboard />
    </RequireAuth>
  );
}

function ProjectsDashboard() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');

  // Create Project Modal state
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectDesc, setNewProjectDesc] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  // Deletion modal state
  const [projectToDelete, setProjectToDelete] = useState<ProjectResponse | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadProjects = async () => {
    try {
      setLoading(true);
      const data = await fetchProjects();
      setProjects(data);
    } catch (e) {
      console.error('Failed to load projects:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadProjects();
  }, []);

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjectName.trim()) return;
    try {
      setIsCreating(true);
      const created = await createProject(newProjectName.trim(), newProjectDesc.trim());
      setNewProjectName('');
      setNewProjectDesc('');
      setShowCreateModal(false);
      await loadProjects();
      if (created?.id) {
        router.push(`/project/${created.id}`);
      }
    } catch (err) {
      alert('Gagal membuat project folder: ' + err);
    } finally {
      setIsCreating(false);
    }
  };

  const confirmDeleteProject = async () => {
    if (!projectToDelete) return;
    try {
      setIsDeleting(true);
      await deleteProject(projectToDelete.id);
      setProjectToDelete(null);
      await loadProjects();
    } catch (err) {
      alert('Gagal menghapus folder: ' + err);
    } finally {
      setIsDeleting(false);
    }
  };

  const filteredProjects = useMemo(() => {
    if (!searchQuery.trim()) return projects;
    const q = searchQuery.toLowerCase();
    return projects.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description && p.description.toLowerCase().includes(q))
    );
  }, [projects, searchQuery]);

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-surface text-foreground">
      {/* Top Navbar */}
      <header className="sticky top-0 z-30 border-b border-border bg-card px-6 py-4 lg:px-8">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-xl font-bold text-primary-foreground shadow-sm">
              P
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold tracking-tight text-foreground">
                  P&amp;ID Studio Web Platform
                </h1>
                <Badge variant="secondary">Drive Workspace</Badge>
              </div>
              <p className="text-xs font-medium text-muted-foreground">
                API RP 970 Corrosion Control Document (CCD) Digitization &amp; Circuitization
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button onClick={() => setShowCreateModal(true)}>
              <FolderPlus className="h-4 w-4" />
              <span className="hidden sm:inline">New Project Folder</span>
            </Button>
            {!isLocalDevAuthBypass() && <Button
              variant="outline"
              onClick={() => {
                setLocalAccessToken(null);
                router.replace('/login');
              }}
              title="Logout"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">Logout</span>
            </Button>}
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 p-6 lg:p-8">
        {/* Drive Explorer Toolbar */}
        <Card>
          <CardContent className="flex flex-col justify-between gap-3 p-4 sm:flex-row sm:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <div className="relative max-w-md flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Cari folder project atau unit..."
                  className="pl-9"
                />
              </div>
              <span className="shrink-0 text-xs font-medium text-muted-foreground">
                {filteredProjects.length} folder{filteredProjects.length === 1 ? '' : 's'}
              </span>
            </div>

            <div className="flex shrink-0 items-center gap-2 self-end sm:self-auto">
              {/* View Mode Toggle */}
              <div className="flex items-center gap-0.5 rounded-md border border-border bg-muted p-0.5">
                <Button
                  type="button"
                  variant={viewMode === 'grid' ? 'secondary' : 'ghost'}
                  size="icon"
                  onClick={() => setViewMode('grid')}
                  className="h-7 w-7"
                  title="Tampilan Kisi (Grid)"
                >
                  <LayoutGrid className="h-4 w-4" />
                </Button>
                <Button
                  type="button"
                  variant={viewMode === 'list' ? 'secondary' : 'ghost'}
                  size="icon"
                  onClick={() => setViewMode('list')}
                  className="h-7 w-7"
                  title="Tampilan Tabel (List)"
                >
                  <ListIcon className="h-4 w-4" />
                </Button>
              </div>

              <Button
                variant="ghost"
                size="icon"
                onClick={() => void loadProjects()}
                disabled={loading}
                title="Refresh daftar folder"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin text-primary' : ''}`} />
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Section Heading */}
        <div className="flex items-center gap-2 text-foreground">
          <FolderOpen className="h-5 w-5 text-primary" />
          <h2 className="text-base font-bold tracking-tight">Plant Projects &amp; Units</h2>
        </div>

        {/* Content body */}
        {loading && projects.length === 0 ? (
          <Card className="flex h-64 flex-col items-center justify-center gap-3 text-muted-foreground">
            <RefreshCw className="h-7 w-7 animate-spin text-primary" />
            <span className="text-sm font-medium">Memuat workspace folder...</span>
          </Card>
        ) : filteredProjects.length === 0 ? (
          <Card className="mx-auto my-8 max-w-md border-dashed p-12 text-center">
            <Folder className="mx-auto mb-3 h-12 w-12 text-muted-foreground/50" />
            <h3 className="text-base font-bold text-foreground">
              {searchQuery ? 'Tidak Ada Folder yang Cocok' : 'Belum Ada Project'}
            </h3>
            <p className="mb-5 mt-1 text-xs text-muted-foreground">
              {searchQuery
                ? `Tidak ditemukan folder dengan nama "${searchQuery}". Coba kata kunci lain.`
                : 'Buat folder project pertama Anda untuk mulai mengunggah dan mendigitasi diagram P&ID.'}
            </p>
            {!searchQuery && (
              <Button size="sm" onClick={() => setShowCreateModal(true)}>
                Buat Folder Project
              </Button>
            )}
          </Card>
        ) : viewMode === 'grid' ? (
          /* ========================================================================= */
          /* GOOGLE DRIVE / EXPLORER STYLE FOLDER GRID                                */
          /* ========================================================================= */
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {/* Quick Action: New Folder Card */}
            <button
              type="button"
              onClick={() => setShowCreateModal(true)}
              className="group flex min-h-[140px] flex-col items-center justify-center rounded-lg border-2 border-dashed border-border p-4 text-center transition hover:border-primary/50 hover:bg-accent/40"
            >
              <div className="mb-2.5 flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground transition group-hover:bg-accent group-hover:text-primary">
                <Plus className="h-6 w-6" />
              </div>
              <span className="text-xs font-semibold text-foreground group-hover:text-primary">
                + New Project Folder
              </span>
              <span className="mt-0.5 text-[10px] text-muted-foreground">
                Tambah workspace baru
              </span>
            </button>

            {/* Folder Cards */}
            {filteredProjects.map((p) => {
              const sheetCount = p.sheets?.length || 0;
              return (
                <Card
                  key={p.id}
                  onClick={() => router.push(`/project/${p.id}`)}
                  className="group relative flex min-h-[140px] cursor-pointer flex-col justify-between transition-all duration-150 hover:border-primary/40 hover:shadow-md"
                >
                  <CardContent className="flex flex-1 flex-col justify-between p-4">
                    {/* Top Bar: Folder Icon & Delete Button */}
                    <div className="flex items-start justify-between">
                      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-amber-200/70 bg-amber-50 text-amber-500 transition-colors group-hover:border-accent group-hover:bg-accent group-hover:text-primary">
                        <Folder className="h-6 w-6 fill-amber-400/30 transition-colors group-hover:fill-primary/20" />
                      </div>

                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={(e) => {
                          e.stopPropagation();
                          setProjectToDelete(p);
                        }}
                        className="h-8 w-8 text-muted-foreground opacity-0 transition hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                        title="Hapus Folder Project"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>

                    {/* Middle: Project Title & Description */}
                    <div className="my-2.5 min-w-0">
                      <h3
                        className="truncate text-sm font-bold text-foreground transition-colors group-hover:text-primary"
                        title={p.name}
                      >
                        {p.name}
                      </h3>
                      <p
                        className="mt-0.5 truncate text-xs text-muted-foreground"
                        title={p.description || 'P&ID drawing sheets & corrosion circuit register'}
                      >
                        {p.description || 'P&ID drawing sheets & corrosion circuits'}
                      </p>
                    </div>

                    {/* Bottom: Metadata Bar (Sheet Count & Upload Time) */}
                    <div className="flex items-center justify-between border-t border-border pt-2 text-[11px] text-muted-foreground">
                      <span className="flex items-center gap-1 font-semibold text-foreground/70">
                        <Layers className="h-3.5 w-3.5 text-muted-foreground" />
                        <span>
                          {sheetCount} Sheet{sheetCount === 1 ? '' : 's'}
                        </span>
                      </span>

                      <span
                        className="flex items-center gap-1 text-muted-foreground"
                        title={`Dibuat / Diunggah: ${formatUploadTime(p.created_at)}`}
                      >
                        <Clock className="h-3 w-3" />
                        <span className="max-w-[110px] truncate">
                          {formatUploadTime(p.created_at)}
                        </span>
                      </span>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        ) : (
          /* ========================================================================= */
          /* WINDOWS EXPLORER / DRIVE STYLE LIST VIEW                                 */
          /* ========================================================================= */
          <Card className="overflow-hidden">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-border bg-muted/60 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  <th className="px-4 py-3">Nama Folder / Project</th>
                  <th className="w-40 px-4 py-3">Jumlah Sheets</th>
                  <th className="w-48 px-4 py-3">Waktu Upload</th>
                  <th className="w-24 px-4 py-3 text-right">Aksi</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border text-xs">
                {filteredProjects.map((p) => {
                  const sheetCount = p.sheets?.length || 0;
                  return (
                    <tr
                      key={p.id}
                      onClick={() => router.push(`/project/${p.id}`)}
                      className="group cursor-pointer transition hover:bg-muted/50"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <Folder className="h-5 w-5 shrink-0 fill-amber-400/20 text-amber-500 transition group-hover:text-primary" />
                          <div className="min-w-0">
                            <span className="block truncate font-semibold text-foreground group-hover:text-primary">
                              {p.name}
                            </span>
                            {p.description && (
                              <span className="block truncate text-[11px] text-muted-foreground">
                                {p.description}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="secondary" className="gap-1.5">
                          <Layers className="h-3 w-3" />
                          <span>
                            {sheetCount} Sheet{sheetCount === 1 ? '' : 's'}
                          </span>
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-[11px] text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3.5 w-3.5" />
                          <span>{formatUploadTime(p.created_at)}</span>
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={(e) => {
                            e.stopPropagation();
                            setProjectToDelete(p);
                          }}
                          className="h-8 w-8 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                          title="Hapus Folder"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        )}
      </main>

      {/* ========================================================================= */}
      {/* MODAL: CREATE PROJECT FOLDER                                              */}
      {/* ========================================================================= */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy-950/40 p-4 backdrop-blur-sm">
          <Card className="w-full max-w-md shadow-xl">
            <CardHeader className="border-b border-border">
              <CardTitle className="flex items-center gap-2.5 text-base">
                <span className="flex h-8 w-8 items-center justify-center rounded-md bg-accent text-primary">
                  <FolderPlus className="h-5 w-5" />
                </span>
                Buat Folder Project Baru
              </CardTitle>
            </CardHeader>

            <CardContent className="pt-6">
              <form onSubmit={handleCreateProject} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="project-name">
                    Nama Project / Plant Unit <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    id="project-name"
                    required
                    placeholder="Contoh: Unit 605 Amine Treating"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="project-desc">Deskripsi / Catatan (Opsional)</Label>
                  <Textarea
                    id="project-desc"
                    rows={3}
                    placeholder="Deskripsi spesifikasi sistem atau nomor gambar..."
                    value={newProjectDesc}
                    onChange={(e) => setNewProjectDesc(e.target.value)}
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setShowCreateModal(false)}
                    disabled={isCreating}
                  >
                    Batal
                  </Button>
                  <Button type="submit" disabled={isCreating || !newProjectName.trim()}>
                    {isCreating && <RefreshCw className="h-3.5 w-3.5 animate-spin" />}
                    <span>Buat Folder</span>
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ========================================================================= */}
      {/* MODAL: DELETE PROJECT CONFIRMATION                                        */}
      {/* ========================================================================= */}
      {projectToDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy-950/40 p-4 backdrop-blur-sm">
          <Card className="w-full max-w-md shadow-xl">
            <CardHeader className="border-b border-border">
              <CardTitle className="flex items-center gap-2.5 text-base">
                <span className="flex h-8 w-8 items-center justify-center rounded-md bg-destructive/10 text-destructive">
                  <AlertTriangle className="h-5 w-5" />
                </span>
                Hapus Folder Project?
              </CardTitle>
            </CardHeader>

            <CardContent className="space-y-4 pt-6">
              <p className="text-xs leading-relaxed text-muted-foreground">
                Folder <strong className="text-foreground">{projectToDelete.name}</strong> dan
                seluruh{' '}
                <strong className="text-foreground">
                  {projectToDelete.sheets?.length || 0} sheet
                </strong>{' '}
                P&ID beserta hasil digitasinya di dalamnya akan dihapus permanen.
              </p>

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setProjectToDelete(null)}
                  disabled={isDeleting}
                >
                  Batal
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  onClick={confirmDeleteProject}
                  disabled={isDeleting}
                >
                  {isDeleting && <RefreshCw className="h-3.5 w-3.5 animate-spin" />}
                  <span>Hapus Folder</span>
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
