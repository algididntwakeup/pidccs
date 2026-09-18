/* eslint-disable @next/next/no-img-element */
'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import {
  Plus,
  Folder,
  Upload,
  Layers,
  ArrowRight,
  Trash2,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Eye,
  FileImage,
} from 'lucide-react';
import { ProjectResponse, SheetResponse } from '@/types/schema';
import {
  fetchProjects,
  createProject,
  uploadSheet,
  deleteProject,
  deleteSheet,
  getRawImageUrl,
} from '@/lib/api';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectDesc, setNewProjectDesc] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);

  // Deletion modals state
  const [projectToDelete, setProjectToDelete] = useState<ProjectResponse | null>(null);
  const [sheetToDelete, setSheetToDelete] = useState<{ projectId: string; sheet: SheetResponse } | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadProjects = async () => {
    try {
      setLoading(true);
      const data = await fetchProjects();
      setProjects(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadProjects();
  }, []);

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjectName.trim()) return;
    try {
      await createProject(newProjectName.trim(), newProjectDesc.trim());
      setNewProjectName('');
      setNewProjectDesc('');
      setShowCreateModal(false);
      await loadProjects();
    } catch (err) {
      alert('Failed to create project: ' + err);
    }
  };

  const handleFileUpload = async (projectId: string, e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setUploading(projectId);
      await uploadSheet(projectId, file);
      await loadProjects();
    } catch (err) {
      alert('Upload failed: ' + err);
    } finally {
      setUploading(null);
      e.target.value = '';
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
      alert('Failed to delete project: ' + err);
    } finally {
      setIsDeleting(false);
    }
  };

  const confirmDeleteSheet = async () => {
    if (!sheetToDelete) return;
    try {
      setIsDeleting(true);
      await deleteSheet(sheetToDelete.projectId, sheetToDelete.sheet.id);
      setSheetToDelete(null);
      await loadProjects();
    } catch (err) {
      alert('Failed to delete sheet: ' + err);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="h-full flex flex-col bg-slate-50 text-slate-900 overflow-y-auto">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-8 py-5 flex items-center justify-between shadow-sm sticky top-0 z-30">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center text-white font-bold text-xl shadow-md">
            P
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900">P&ID Studio Web Platform</h1>
            <p className="text-xs text-slate-500 font-medium">
              API RP 970 Corrosion Control Document (CCD) Digitization & Circuitization
            </p>
          </div>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg font-medium text-sm flex items-center space-x-2 shadow transition"
        >
          <Plus className="w-4 h-4" />
          <span>New Project</span>
        </button>
      </header>

      {/* Main Container */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-8 space-y-8">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-800 flex items-center space-x-2">
            <Folder className="w-5 h-5 text-indigo-600" />
            <span>Plant Projects & Units</span>
          </h2>
          <span className="text-sm font-medium text-slate-500">{projects.length} workspace(s)</span>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 text-slate-400 space-y-3">
            <RefreshCw className="w-6 h-6 animate-spin text-indigo-600" />
            <span className="text-sm font-medium">Loading project workspaces...</span>
          </div>
        ) : projects.length === 0 ? (
          <div className="bg-white rounded-2xl border-2 border-dashed border-slate-200 p-12 text-center max-w-md mx-auto my-12 shadow-sm">
            <Layers className="w-12 h-12 text-slate-300 mx-auto mb-3" />
            <h3 className="text-base font-semibold text-slate-800">No Projects Yet</h3>
            <p className="text-sm text-slate-500 mt-1 mb-4">
              Create your first project workspace to start digitizing P&ID diagrams.
            </p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 transition"
            >
              Create Workspace
            </button>
          </div>
        ) : (
          <div className="space-y-8">
            {projects.map((p) => (
              <div
                key={p.id}
                className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm hover:shadow-md transition duration-200"
              >
                {/* Project Workspace Bar */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-slate-100 gap-3">
                  <div className="flex items-start space-x-3">
                    <div className="w-10 h-10 rounded-xl bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 shrink-0 mt-0.5">
                      <Folder className="w-5 h-5" />
                    </div>
                    <div>
                      <div className="flex items-center space-x-2.5">
                        <h3 className="font-bold text-slate-900 text-base">{p.name}</h3>
                        <span className="text-xs bg-slate-100 text-slate-700 font-semibold px-2.5 py-0.5 rounded-full border border-slate-200">
                          {p.sheets?.length || 0} sheet{(p.sheets?.length || 0) === 1 ? '' : 's'}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 mt-0.5">
                        {p.description || 'P&ID drawing sheets & corrosion circuit register'}
                      </p>
                    </div>
                  </div>

                  {/* Project Quick Actions */}
                  <div className="flex items-center space-x-2 shrink-0">
                    <label className="cursor-pointer text-xs font-semibold bg-indigo-50 text-indigo-700 hover:bg-indigo-100 px-3 py-1.5 rounded-lg flex items-center space-x-1.5 transition border border-indigo-200">
                      <Upload className="w-3.5 h-3.5" />
                      <span>{uploading === p.id ? 'Uploading...' : 'Upload P&ID'}</span>
                      <input
                        type="file"
                        accept=".pdf,.png,.jpg,.jpeg"
                        className="hidden"
                        disabled={uploading === p.id}
                        onChange={(e) => handleFileUpload(p.id, e)}
                      />
                    </label>

                    {p.sheets && p.sheets.length > 0 && (
                      <Link
                        href={`/project/${p.id}?sheetId=${p.sheets[0].id}`}
                        className="text-xs font-semibold bg-slate-900 text-white hover:bg-indigo-600 px-3 py-1.5 rounded-lg flex items-center space-x-1.5 transition shadow-sm"
                      >
                        <span>Open Workspace</span>
                        <ArrowRight className="w-3.5 h-3.5" />
                      </Link>
                    )}

                    <button
                      onClick={() => setProjectToDelete(p)}
                      className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition"
                      title="Delete Project Workspace"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Google Drive-Style P&ID Sheets Grid */}
                <div className="mt-5">
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
                    {/* Sheet Cards */}
                    {p.sheets &&
                      p.sheets.map((s) => {
                        const rawUrl = getRawImageUrl(p.id, s.id);
                        const isDetected = s.status === 'detected';
                        return (
                          <div
                            key={s.id}
                            className="group relative bg-slate-50 border border-slate-200 rounded-xl overflow-hidden hover:shadow-lg hover:border-indigo-300 transition duration-200 flex flex-col justify-between"
                          >
                            {/* Thumbnail Area (Google Drive style card preview) */}
                            <Link
                              href={`/project/${p.id}?sheetId=${s.id}`}
                              className="block relative aspect-[4/3] bg-slate-900/5 overflow-hidden border-b border-slate-200"
                            >
                              <img
                                src={rawUrl}
                                alt={s.filename}
                                className="w-full h-full object-contain p-1.5 group-hover:scale-105 transition-transform duration-300"
                              />

                              {/* Status Badge on Thumbnail */}
                              <div className="absolute top-2 left-2 z-10">
                                <span
                                  className={`text-[10px] font-bold px-2 py-0.5 rounded-md shadow-sm uppercase tracking-wider flex items-center space-x-1 ${
                                    isDetected
                                      ? 'bg-emerald-600 text-white'
                                      : s.status === 'detecting'
                                      ? 'bg-amber-500 text-white animate-pulse'
                                      : 'bg-slate-700 text-white'
                                  }`}
                                >
                                  {isDetected && <CheckCircle2 className="w-3 h-3 inline" />}
                                  <span>{s.status}</span>
                                </span>
                              </div>

                              {/* Hover Quick View Overlay */}
                              <div className="absolute inset-0 bg-slate-900/30 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                                <span className="bg-white/90 text-slate-900 text-xs font-semibold px-3 py-1.5 rounded-lg shadow-md flex items-center space-x-1.5">
                                  <Eye className="w-3.5 h-3.5 text-indigo-600" />
                                  <span>Open in Studio</span>
                                </span>
                              </div>
                            </Link>

                            {/* Sheet Details & Action Bar */}
                            <div className="p-3.5 bg-white flex-1 flex flex-col justify-between">
                              <div className="flex items-start justify-between gap-2">
                                <Link
                                  href={`/project/${p.id}?sheetId=${s.id}`}
                                  className="font-semibold text-xs text-slate-900 hover:text-indigo-600 truncate flex-1"
                                  title={s.filename}
                                >
                                  {s.filename}
                                </Link>

                                {/* Delete Sheet Quick Button */}
                                <button
                                  onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    setSheetToDelete({ projectId: p.id, sheet: s });
                                  }}
                                  className="text-slate-300 hover:text-rose-600 p-1 hover:bg-rose-50 rounded transition shrink-0"
                                  title="Delete this P&ID Sheet"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </button>
                              </div>

                              <div className="mt-2 pt-2 border-t border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
                                <span className="flex items-center space-x-1">
                                  <FileImage className="w-3 h-3 text-slate-400" />
                                  <span>
                                    {s.width && s.height
                                      ? `${s.width}×${s.height}`
                                      : 'P&ID Drawing'}
                                  </span>
                                </span>
                                <Link
                                  href={`/project/${p.id}?sheetId=${s.id}`}
                                  className="text-indigo-600 hover:text-indigo-800 font-semibold flex items-center space-x-0.5"
                                >
                                  <span>Studio</span>
                                  <ArrowRight className="w-3 h-3" />
                                </Link>
                              </div>
                            </div>
                          </div>
                        );
                      })}

                    {/* Quick Upload Card (Google Drive New File style) */}
                    <label
                      className={`aspect-[4/3] sm:aspect-auto min-h-[180px] border-2 border-dashed border-slate-200 hover:border-indigo-400 bg-slate-50/50 hover:bg-indigo-50/30 rounded-xl flex flex-col items-center justify-center cursor-pointer transition p-4 text-center group ${
                        uploading === p.id ? 'opacity-50 pointer-events-none' : ''
                      }`}
                    >
                      <div className="w-10 h-10 rounded-full bg-white group-hover:bg-indigo-100 border border-slate-200 group-hover:border-indigo-300 flex items-center justify-center text-slate-400 group-hover:text-indigo-600 mb-2 transition shadow-sm">
                        {uploading === p.id ? (
                          <RefreshCw className="w-5 h-5 animate-spin text-indigo-600" />
                        ) : (
                          <Upload className="w-5 h-5" />
                        )}
                      </div>
                      <span className="text-xs font-semibold text-slate-700 group-hover:text-indigo-700">
                        {uploading === p.id ? 'Uploading drawing...' : '+ Add P&ID Sheet'}
                      </span>
                      <span className="text-[10px] text-slate-400 mt-1">PDF, PNG, JPG up to 350 DPI</span>
                      <input
                        type="file"
                        accept=".pdf,.png,.jpg,.jpeg"
                        className="hidden"
                        disabled={uploading === p.id}
                        onChange={(e) => handleFileUpload(p.id, e)}
                      />
                    </label>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* Create Project Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4">
            <h3 className="text-lg font-bold text-slate-900">Create New Project Workspace</h3>
            <form onSubmit={handleCreateProject} className="space-y-4">
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1">
                  Project / Plant Unit Name
                </label>
                <input
                  type="text"
                  placeholder="e.g. Unit 605 Amine Treating"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  autoFocus
                  required
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1">
                  Description (Optional)
                </label>
                <input
                  type="text"
                  placeholder="e.g. Acid Gas Enrichment & Solvent Regeneration"
                  value={newProjectDesc}
                  onChange={(e) => setNewProjectDesc(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div className="flex justify-end space-x-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition"
                >
                  Create Workspace
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Project Confirmation Modal */}
      {projectToDelete && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4 border border-rose-100">
            <div className="flex items-center space-x-3 text-rose-600">
              <div className="w-10 h-10 rounded-xl bg-rose-50 flex items-center justify-center shrink-0">
                <AlertTriangle className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-900">Delete Project Workspace?</h3>
                <p className="text-xs text-slate-500">This action cannot be undone.</p>
              </div>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              Are you sure you want to permanently delete{' '}
              <strong className="text-slate-900 font-semibold">{projectToDelete.name}</strong>?
              All{' '}
              <span className="font-semibold text-rose-600">
                {projectToDelete.sheets?.length || 0} P&ID drawing sheet(s)
              </span>
              , OCR tag recognitions, and corrosion circuit lines will be completely removed.
            </p>

            <div className="flex justify-end space-x-2 pt-3 border-t border-slate-100">
              <button
                type="button"
                disabled={isDeleting}
                onClick={() => setProjectToDelete(null)}
                className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeleting}
                onClick={confirmDeleteProject}
                className="px-4 py-2 text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white rounded-lg transition shadow flex items-center space-x-1.5"
              >
                {isDeleting && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>{isDeleting ? 'Deleting...' : 'Delete Permanently'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Sheet Confirmation Modal */}
      {sheetToDelete && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4 border border-rose-100">
            <div className="flex items-center space-x-3 text-rose-600">
              <div className="w-10 h-10 rounded-xl bg-rose-50 flex items-center justify-center shrink-0">
                <AlertTriangle className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-900">Delete P&ID Drawing Sheet?</h3>
                <p className="text-xs text-slate-500">This action cannot be undone.</p>
              </div>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              Are you sure you want to delete drawing sheet{' '}
              <strong className="text-slate-900 font-semibold">{sheetToDelete.sheet.filename}</strong>?
              Raw diagram tiles and all associated corrosion line circuit data for this sheet will be permanently deleted.
            </p>

            <div className="flex justify-end space-x-2 pt-3 border-t border-slate-100">
              <button
                type="button"
                disabled={isDeleting}
                onClick={() => setSheetToDelete(null)}
                className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeleting}
                onClick={confirmDeleteSheet}
                className="px-4 py-2 text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white rounded-lg transition shadow flex items-center space-x-1.5"
              >
                {isDeleting && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>{isDeleting ? 'Deleting...' : 'Delete Sheet'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
