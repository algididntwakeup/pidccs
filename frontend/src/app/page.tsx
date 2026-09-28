'use client';

import { useState, useEffect, useMemo } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Plus,
  Folder,
  FolderPlus,
  Layers,
  ArrowRight,
  Trash2,
  RefreshCw,
  Search,
  Clock,
  LayoutGrid,
  List as ListIcon,
  FolderOpen,
  Calendar,
  AlertTriangle,
} from 'lucide-react';
import { ProjectResponse } from '@/types/schema';
import {
  fetchProjects,
  createProject,
  deleteProject,
} from '@/lib/api';

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
    <div className="h-full flex flex-col bg-slate-50 text-slate-900 overflow-y-auto">
      {/* Top Navbar */}
      <header className="bg-white border-b border-slate-200 px-6 lg:px-8 py-4 flex items-center justify-between shadow-xs sticky top-0 z-30">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center text-white font-bold text-xl shadow-md">
            P
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-lg font-bold tracking-tight text-slate-900">
                P&ID Studio Web Platform
              </h1>
              <span className="text-[11px] font-semibold bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded border border-indigo-200/60">
                Drive Workspace
              </span>
            </div>
            <p className="text-xs text-slate-500 font-medium">
              API RP 970 Corrosion Control Document (CCD) Digitization & Circuitization
            </p>
          </div>
        </div>

        <button
          onClick={() => setShowCreateModal(true)}
          className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg font-semibold text-sm flex items-center space-x-2 shadow-xs transition"
        >
          <FolderPlus className="w-4 h-4" />
          <span>New Project Folder</span>
        </button>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 lg:p-8 space-y-6">
        {/* Drive Explorer Toolbar */}
        <div className="bg-white border border-slate-200 rounded-xl p-3.5 shadow-2xs flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center space-x-3 flex-1 min-w-0">
            <div className="relative flex-1 max-w-md">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Cari folder project atau unit..."
                className="w-full pl-9 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition"
              />
            </div>
            <span className="text-xs font-medium text-slate-500 shrink-0">
              {filteredProjects.length} folder{filteredProjects.length === 1 ? '' : 's'}
            </span>
          </div>

          <div className="flex items-center space-x-2 shrink-0 self-end sm:self-auto">
            {/* View Mode Toggle */}
            <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200">
              <button
                type="button"
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-md text-xs font-medium flex items-center transition ${
                  viewMode === 'grid'
                    ? 'bg-white text-indigo-600 shadow-xs'
                    : 'text-slate-500 hover:text-slate-900'
                }`}
                title="Tampilan Kisi (Grid)"
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded-md text-xs font-medium flex items-center transition ${
                  viewMode === 'list'
                    ? 'bg-white text-indigo-600 shadow-xs'
                    : 'text-slate-500 hover:text-slate-900'
                }`}
                title="Tampilan Tabel (List)"
              >
                <ListIcon className="w-4 h-4" />
              </button>
            </div>

            <button
              onClick={() => void loadProjects()}
              disabled={loading}
              className="p-1.5 text-slate-500 hover:text-indigo-600 hover:bg-slate-100 rounded-lg transition"
              title="Refresh daftar folder"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-600' : ''}`} />
            </button>
          </div>
        </div>

        {/* Section Heading */}
        <div className="flex items-center space-x-2 text-slate-700">
          <FolderOpen className="w-5 h-5 text-indigo-600" />
          <h2 className="text-base font-bold tracking-tight">Plant Projects &amp; Units</h2>
        </div>

        {/* Content body */}
        {loading && projects.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-slate-400 space-y-3 bg-white rounded-2xl border border-slate-200">
            <RefreshCw className="w-7 h-7 animate-spin text-indigo-600" />
            <span className="text-sm font-medium">Memuat workspace folder...</span>
          </div>
        ) : filteredProjects.length === 0 ? (
          <div className="bg-white rounded-2xl border-2 border-dashed border-slate-200 p-12 text-center max-w-md mx-auto my-8 shadow-xs">
            <Folder className="w-12 h-12 text-slate-300 mx-auto mb-3" />
            <h3 className="text-base font-bold text-slate-800">
              {searchQuery ? 'Tidak Ada Folder yang Cocok' : 'Belum Ada Project'}
            </h3>
            <p className="text-xs text-slate-500 mt-1 mb-5">
              {searchQuery
                ? `Tidak ditemukan folder dengan nama "${searchQuery}". Coba kata kunci lain.`
                : 'Buat folder project pertama Anda untuk mulai mengunggah dan mendigitasi diagram P&ID.'}
            </p>
            {!searchQuery && (
              <button
                onClick={() => setShowCreateModal(true)}
                className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-xs font-semibold shadow-xs transition"
              >
                Buat Folder Project
              </button>
            )}
          </div>
        ) : viewMode === 'grid' ? (
          /* ========================================================================= */
          /* GOOGLE DRIVE / EXPLORER STYLE FOLDER GRID                                */
          /* ========================================================================= */
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {/* Quick Action: New Folder Card */}
            <button
              type="button"
              onClick={() => setShowCreateModal(true)}
              className="border-2 border-dashed border-slate-200 hover:border-indigo-400 hover:bg-indigo-50/20 rounded-xl p-4 flex flex-col items-center justify-center text-center transition group min-h-[140px]"
            >
              <div className="w-11 h-11 rounded-full bg-slate-100 group-hover:bg-indigo-100 text-slate-400 group-hover:text-indigo-600 flex items-center justify-center mb-2.5 transition">
                <Plus className="w-6 h-6" />
              </div>
              <span className="text-xs font-semibold text-slate-700 group-hover:text-indigo-600">
                + New Project Folder
              </span>
              <span className="text-[10px] text-slate-400 mt-0.5">Tambah workspace baru</span>
            </button>

            {/* Folder Cards */}
            {filteredProjects.map((p) => {
              const sheetCount = p.sheets?.length || 0;
              return (
                <div
                  key={p.id}
                  onClick={() => router.push(`/project/${p.id}`)}
                  className="bg-white hover:bg-slate-50/80 border border-slate-200 hover:border-indigo-300 rounded-xl p-4 shadow-2xs hover:shadow-md transition-all duration-150 flex flex-col justify-between group cursor-pointer relative min-h-[140px]"
                >
                  {/* Top Bar: Folder Icon & Delete Button */}
                  <div className="flex items-start justify-between">
                    <div className="w-11 h-11 rounded-xl bg-amber-50 border border-amber-200/70 flex items-center justify-center text-amber-500 group-hover:bg-indigo-50 group-hover:border-indigo-200 group-hover:text-indigo-600 transition-colors shrink-0">
                      <Folder className="w-6 h-6 fill-amber-400/30 group-hover:fill-indigo-500/20 transition-colors" />
                    </div>

                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setProjectToDelete(p);
                      }}
                      className="opacity-0 group-hover:opacity-100 p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition"
                      title="Hapus Folder Project"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>

                  {/* Middle: Project Title & Description */}
                  <div className="my-2.5 min-w-0">
                    <h3
                      className="font-bold text-slate-900 text-sm group-hover:text-indigo-600 truncate transition-colors"
                      title={p.name}
                    >
                      {p.name}
                    </h3>
                    <p
                      className="text-xs text-slate-500 truncate mt-0.5"
                      title={p.description || 'P&ID drawing sheets & corrosion circuit register'}
                    >
                      {p.description || 'P&ID drawing sheets & corrosion circuits'}
                    </p>
                  </div>

                  {/* Bottom: Metadata Bar (Sheet Count & Upload Time) */}
                  <div className="pt-2 border-t border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
                    <span className="flex items-center space-x-1 font-semibold text-slate-600">
                      <Layers className="w-3.5 h-3.5 text-slate-400" />
                      <span>{sheetCount} Sheet{sheetCount === 1 ? '' : 's'}</span>
                    </span>

                    <span
                      className="flex items-center space-x-1 text-slate-400"
                      title={`Dibuat / Diunggah: ${formatUploadTime(p.created_at)}`}
                    >
                      <Clock className="w-3 h-3 text-slate-400" />
                      <span className="truncate max-w-[110px]">{formatUploadTime(p.created_at)}</span>
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* ========================================================================= */
          /* WINDOWS EXPLORER / DRIVE STYLE LIST VIEW                                 */
          /* ========================================================================= */
          <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-slate-50/80 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                  <th className="py-3 px-4">Nama Folder / Project</th>
                  <th className="py-3 px-4 w-40">Jumlah Sheets</th>
                  <th className="py-3 px-4 w-48">Waktu Upload</th>
                  <th className="py-3 px-4 text-right w-24">Aksi</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-xs">
                {filteredProjects.map((p) => {
                  const sheetCount = p.sheets?.length || 0;
                  return (
                    <tr
                      key={p.id}
                      onClick={() => router.push(`/project/${p.id}`)}
                      className="hover:bg-slate-50 cursor-pointer transition group"
                    >
                      <td className="py-3 px-4">
                        <div className="flex items-center space-x-3">
                          <Folder className="w-5 h-5 text-amber-500 fill-amber-400/20 group-hover:text-indigo-600 shrink-0 transition" />
                          <div className="min-w-0">
                            <span className="font-semibold text-slate-900 group-hover:text-indigo-600 truncate block">
                              {p.name}
                            </span>
                            {p.description && (
                              <span className="text-[11px] text-slate-400 truncate block">
                                {p.description}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        <span className="inline-flex items-center space-x-1.5 bg-slate-100 text-slate-700 font-semibold px-2 py-0.5 rounded text-[11px]">
                          <Layers className="w-3 h-3 text-slate-500" />
                          <span>{sheetCount} Sheet{sheetCount === 1 ? '' : 's'}</span>
                        </span>
                      </td>
                      <td className="py-3 px-4 text-slate-500 text-[11px]">
                        <span className="flex items-center space-x-1">
                          <Clock className="w-3.5 h-3.5 text-slate-400" />
                          <span>{formatUploadTime(p.created_at)}</span>
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setProjectToDelete(p);
                          }}
                          className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition"
                          title="Hapus Folder"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>

      {/* ========================================================================= */}
      {/* MODAL: CREATE PROJECT FOLDER                                              */}
      {/* ========================================================================= */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 max-w-md w-full p-6 space-y-4">
            <div className="flex items-center space-x-2.5 pb-2 border-b border-slate-100">
              <div className="w-8 h-8 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center">
                <FolderPlus className="w-5 h-5" />
              </div>
              <h3 className="text-base font-bold text-slate-900">Buat Folder Project Baru</h3>
            </div>

            <form onSubmit={handleCreateProject} className="space-y-4">
              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1">
                  Nama Project / Plant Unit <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  required
                  placeholder="Contoh: Unit 605 Amine Treating"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  className="w-full px-3.5 py-2 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-600 transition"
                />
              </div>

              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1">
                  Deskripsi / Catatan (Opsional)
                </label>
                <textarea
                  rows={3}
                  placeholder="Deskripsi spesifikasi sistem atau nomor gambar..."
                  value={newProjectDesc}
                  onChange={(e) => setNewProjectDesc(e.target.value)}
                  className="w-full px-3.5 py-2 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-600 transition"
                />
              </div>

              <div className="flex justify-end space-x-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  disabled={isCreating}
                  className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
                >
                  Batal
                </button>
                <button
                  type="submit"
                  disabled={isCreating || !newProjectName.trim()}
                  className="px-4 py-2 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white rounded-lg transition flex items-center space-x-1.5 shadow-xs"
                >
                  {isCreating && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                  <span>Buat Folder</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* MODAL: DELETE PROJECT CONFIRMATION                                        */}
      {/* ========================================================================= */}
      {projectToDelete && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl border border-slate-200 max-w-md w-full p-6 space-y-4">
            <div className="flex items-center space-x-2.5 pb-2 border-b border-slate-100 text-rose-600">
              <div className="w-8 h-8 rounded-lg bg-rose-50 text-rose-600 flex items-center justify-center">
                <AlertTriangle className="w-5 h-5" />
              </div>
              <h3 className="text-base font-bold text-slate-900">Hapus Folder Project?</h3>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              Folder <strong className="text-slate-900">{projectToDelete.name}</strong> dan seluruh{' '}
              <strong className="text-slate-900">{projectToDelete.sheets?.length || 0} sheet</strong>{' '}
              P&ID beserta hasil digitasinya di dalamnya akan dihapus permanen.
            </p>

            <div className="flex justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={() => setProjectToDelete(null)}
                disabled={isDeleting}
                className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Batal
              </button>
              <button
                type="button"
                onClick={confirmDeleteProject}
                disabled={isDeleting}
                className="px-4 py-2 text-xs font-semibold bg-rose-600 hover:bg-rose-700 disabled:bg-rose-300 text-white rounded-lg transition flex items-center space-x-1.5 shadow-xs"
              >
                {isDeleting && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>Hapus Folder</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
