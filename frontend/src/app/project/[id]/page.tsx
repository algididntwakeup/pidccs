/* eslint-disable @next/next/no-img-element */
'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  ArrowLeft,
  Upload,
  Layers,
  Trash2,
  RefreshCw,
  ScanSearch,
  CheckCircle2,
  AlertTriangle,
  FolderOpen,
  FileImage,
} from 'lucide-react';
import { ProjectResponse, SheetResponse } from '@/types/schema';
import { fetchProject, fetchSheets, uploadSheet, deleteSheet, getThumbnailUrl } from '@/lib/api';

/**
 * Project Folder View — the "folder" a Project now behaves as.
 *
 * A Project is a container of Sheets; one PDF with N pages becomes N Sheets at
 * upload time, so this page is the only sane entry point: it lists every page as a
 * thumbnail card and the canvas is opened per sheet
 * (`/project/{id}/sheet/{sheet_id}`). Rendering the canvas here directly would mean
 * rendering an arbitrary page of a possibly 50-page bundle.
 */
export default function ProjectFolderPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [sheets, setSheets] = useState<SheetResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState('');
  const [sheetToDelete, setSheetToDelete] = useState<SheetResponse | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      const [p, s] = await Promise.all([fetchProject(projectId), fetchSheets(projectId)]);
      setProject(p);
      setSheets(s);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (projectId) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      setUploading(true);
      setUploadMsg(`Mengunggah ${file.name}…`);
      const created = await uploadSheet(projectId, file);
      setUploadMsg(
        created.length > 1
          ? `${created.length} halaman dipecah menjadi ${created.length} sheet.`
          : `${created.length} sheet ditambahkan.`
      );
      await load();
      window.setTimeout(() => setUploadMsg(''), 4000);
    } catch (err) {
      alert('Upload gagal: ' + (err instanceof Error ? err.message : String(err)));
      setUploadMsg('');
    } finally {
      setUploading(false);
    }
  };

  const confirmDelete = async () => {
    if (!sheetToDelete) return;
    try {
      setDeleting(true);
      await deleteSheet(projectId, sheetToDelete.id);
      setSheetToDelete(null);
      await load();
    } catch (err) {
      alert('Gagal menghapus sheet: ' + (err instanceof Error ? err.message : String(err)));
    } finally {
      setDeleting(false);
    }
  };

  const detected = sheets.filter((s) => s.status === 'detected').length;

  return (
    <div className="h-full flex flex-col bg-slate-50 text-slate-900 overflow-hidden">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-4 lg:px-8 py-4 shadow-sm z-20 shrink-0">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center space-x-3 min-w-0">
            <Link
              href="/"
              className="p-1.5 hover:bg-slate-100 text-slate-600 rounded-lg transition shrink-0"
              title="Back to Projects"
            >
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <div className="w-9 h-9 bg-indigo-600 rounded-xl flex items-center justify-center shrink-0">
              <FolderOpen className="w-5 h-5 text-white" />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-bold tracking-tight text-slate-900 truncate">
                {project?.name || 'Loading…'}
              </h1>
              <p className="text-xs text-slate-500 truncate">
                {project?.description || 'Project folder'}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <span className="text-xs font-semibold bg-slate-100 text-slate-600 px-2.5 py-1 rounded-lg">
              {sheets.length} sheet
            </span>
            <span className="text-xs font-semibold bg-emerald-50 text-emerald-700 px-2.5 py-1 rounded-lg">
              {detected} ter-digitasi
            </span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg"
              className="hidden"
              onChange={handleUpload}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white px-3.5 py-2 rounded-lg flex items-center space-x-1.5 transition shadow-sm"
              title="Unggah PDF / PNG / JPG. PDF multi-halaman otomatis dipecah per halaman."
            >
              {uploading ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Upload className="w-3.5 h-3.5" />
              )}
              <span>Tambah P&amp;ID Sheet</span>
            </button>
          </div>
        </div>
        {uploadMsg && (
          <p className="mt-2 text-xs font-medium text-indigo-700 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-1.5">
            {uploadMsg}
          </p>
        )}
      </header>

      {/* Sheet grid */}
      <div className="flex-1 overflow-y-auto p-4 lg:p-8">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-slate-500 text-sm">
            <RefreshCw className="w-4 h-4 animate-spin mr-2" /> Memuat sheet…
          </div>
        ) : sheets.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-center border-2 border-dashed border-slate-300 rounded-2xl bg-white">
            <FileImage className="w-10 h-10 text-slate-300 mb-3" />
            <p className="font-semibold text-slate-700">Belum ada sheet di folder ini</p>
            <p className="text-xs text-slate-500 mt-1 max-w-sm">
              Unggah P&amp;ID. Satu PDF berisi banyak halaman akan otomatis dipecah menjadi satu
              sheet per halaman supaya kanvas tidak perlu memuat puluhan gambar sekaligus.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {sheets.map((s) => {
              const isDetected = s.status === 'detected';
              const isDetecting = s.status === 'detecting';
              return (
                <div
                  key={s.id}
                  className="group bg-white border border-slate-200 rounded-xl overflow-hidden hover:shadow-lg hover:border-indigo-300 transition duration-200 flex flex-col"
                >
                  <button
                    onClick={() => router.push(`/project/${projectId}/sheet/${s.id}`)}
                    className="block relative aspect-[4/3] bg-slate-900/5 overflow-hidden border-b border-slate-200 cursor-pointer"
                    title={`Buka kanvas: ${s.filename}`}
                  >
                    <img
                      src={getThumbnailUrl(projectId, s.id, 480)}
                      alt={s.filename}
                      loading="lazy"
                      className="w-full h-full object-contain p-1.5 group-hover:scale-105 transition-transform duration-300"
                    />
                    <div className="absolute top-2 left-2 z-10">
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-md shadow-sm uppercase tracking-wider flex items-center space-x-1 ${
                          isDetected
                            ? 'bg-emerald-600 text-white'
                            : isDetecting
                            ? 'bg-amber-500 text-white animate-pulse'
                            : 'bg-slate-700 text-white'
                        }`}
                      >
                        {isDetected ? (
                          <CheckCircle2 className="w-3 h-3" />
                        ) : isDetecting ? (
                          <RefreshCw className="w-3 h-3 animate-spin" />
                        ) : (
                          <AlertTriangle className="w-3 h-3" />
                        )}
                        <span>{s.status}</span>
                      </span>
                    </div>
                  </button>

                  <div className="p-2.5 flex-1 flex flex-col justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-slate-800 truncate" title={s.filename}>
                        {s.filename}
                      </p>
                      <div className="flex items-center space-x-1.5 mt-1 flex-wrap">
                        {s.sheet_number && (
                          <span className="text-[10px] font-bold bg-indigo-50 text-indigo-700 px-1.5 py-0.5 rounded">
                            Hal. {s.sheet_number}
                          </span>
                        )}
                        {s.width && s.height ? (
                          <span className="text-[10px] text-slate-500 font-mono">
                            {s.width}×{s.height}
                          </span>
                        ) : null}
                      </div>
                    </div>

                    <div className="flex items-center justify-between">
                      <button
                        onClick={() => router.push(`/project/${projectId}/sheet/${s.id}`)}
                        className="text-[11px] font-semibold bg-slate-900 hover:bg-indigo-600 text-white px-2.5 py-1 rounded-lg flex items-center space-x-1 transition"
                      >
                        <ScanSearch className="w-3 h-3" />
                        <span>Open Studio</span>
                      </button>
                      <button
                        onClick={() => setSheetToDelete(s)}
                        className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition"
                        title="Hapus sheet ini"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Delete confirmation */}
      {sheetToDelete && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center space-x-2">
              <Trash2 className="w-5 h-5 text-rose-600" />
              <h3 className="font-bold text-slate-900">Hapus sheet ini?</h3>
            </div>
            <p className="text-sm text-slate-600">
              <span className="font-semibold">{sheetToDelete.filename}</span>
              {sheetToDelete.sheet_number ? ` (Hal. ${sheetToDelete.sheet_number})` : ''} beserta
              hasil digitasinya akan dihapus permanen.
            </p>
            <div className="flex justify-end space-x-2">
              <button
                onClick={() => setSheetToDelete(null)}
                className="px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Batal
              </button>
              <button
                onClick={confirmDelete}
                disabled={deleting}
                className="px-4 py-2 text-sm font-semibold bg-rose-600 hover:bg-rose-700 disabled:bg-rose-300 text-white rounded-lg transition flex items-center space-x-1.5"
              >
                {deleting && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>Hapus</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
