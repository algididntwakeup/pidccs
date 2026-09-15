'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Plus, Folder, FileText, Upload, Clock, Layers, ArrowRight } from 'lucide-react';
import { ProjectResponse } from '@/types/schema';
import { fetchProjects, createProject, uploadSheet } from '@/lib/api';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [newProjectName, setNewProjectName] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);

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
      await createProject(newProjectName.trim());
      setNewProjectName('');
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
    }
  };

  return (
    <div className="h-full flex flex-col bg-slate-50 text-slate-900 overflow-y-auto">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-8 py-5 flex items-center justify-between shadow-sm">
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
      <main className="flex-1 max-w-7xl w-full mx-auto p-8 space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-800 flex items-center space-x-2">
            <Folder className="w-5 h-5 text-indigo-600" />
            <span>Plant Projects & Units</span>
          </h2>
          <span className="text-sm font-medium text-slate-500">{projects.length} workspace(s)</span>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-64 text-slate-400">Loading projects...</div>
        ) : projects.length === 0 ? (
          <div className="bg-white rounded-2xl border-2 border-dashed border-slate-200 p-12 text-center max-w-md mx-auto my-12">
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
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((p) => (
              <div
                key={p.id}
                className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm hover:shadow-md transition flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-start justify-between">
                    <h3 className="font-bold text-slate-900 text-base">{p.name}</h3>
                    <span className="text-xs bg-slate-100 text-slate-600 font-semibold px-2 py-0.5 rounded-full">
                      {p.sheets?.length || 0} sheets
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-1 line-clamp-2">
                    {p.description || 'No description provided'}
                  </p>

                  {/* Sheets preview list */}
                  <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
                    {p.sheets && p.sheets.length > 0 ? (
                      p.sheets.slice(0, 3).map((s) => (
                        <Link
                          key={s.id}
                          href={`/project/${p.id}?sheetId=${s.id}`}
                          className="flex items-center justify-between p-2 rounded-lg bg-slate-50 hover:bg-indigo-50 group text-xs text-slate-700 transition"
                        >
                          <div className="flex items-center space-x-2 truncate">
                            <FileText className="w-3.5 h-3.5 text-slate-400 group-hover:text-indigo-600 shrink-0" />
                            <span className="truncate font-medium">{s.filename}</span>
                          </div>
                          <span
                            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded uppercase ${
                              s.status === 'detected'
                                ? 'bg-emerald-100 text-emerald-700'
                                : s.status === 'detecting'
                                ? 'bg-amber-100 text-amber-700 animate-pulse'
                                : 'bg-slate-200 text-slate-600'
                            }`}
                          >
                            {s.status}
                          </span>
                        </Link>
                      ))
                    ) : (
                      <p className="text-xs text-slate-400 italic py-1">No drawing sheets uploaded yet</p>
                    )}
                    {p.sheets && p.sheets.length > 3 && (
                      <p className="text-[11px] text-slate-400 text-center">
                        +{p.sheets.length - 3} more sheets
                      </p>
                    )}
                  </div>
                </div>

                {/* Card Actions */}
                <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between">
                  <label className="cursor-pointer text-xs font-semibold text-indigo-600 hover:text-indigo-800 flex items-center space-x-1">
                    <Upload className="w-3.5 h-3.5" />
                    <span>{uploading === p.id ? 'Uploading...' : 'Add P&ID'}</span>
                    <input
                      type="file"
                      accept=".pdf,.png,.jpg,.jpeg"
                      className="hidden"
                      disabled={uploading === p.id}
                      onChange={(e) => handleFileUpload(p.id, e)}
                    />
                  </label>

                  {p.sheets && p.sheets.length > 0 ? (
                    <Link
                      href={`/project/${p.id}?sheetId=${p.sheets[0].id}`}
                      className="text-xs font-semibold bg-slate-900 text-white hover:bg-indigo-600 px-3 py-1.5 rounded-lg flex items-center space-x-1 transition"
                    >
                      <span>Open Workspace</span>
                      <ArrowRight className="w-3 h-3" />
                    </Link>
                  ) : (
                    <span className="text-xs text-slate-400">Upload sheet to open</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* Create Modal */}
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
              <div className="flex justify-end space-x-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg"
                >
                  Create
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
