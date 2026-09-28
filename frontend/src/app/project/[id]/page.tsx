/* eslint-disable @next/next/no-img-element */
'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  ArrowLeft,
  Upload,
  Trash2,
  RefreshCw,
  ScanSearch,
  CheckCircle2,
  AlertTriangle,
  FolderOpen,
  FileImage,
  Clock,
  Lock,
} from 'lucide-react';
import { ProjectResponse, SheetResponse } from '@/types/schema';
import { fetchProject, fetchSheets, uploadSheet, deleteSheet, getThumbnailUrl } from '@/lib/api';

/**
 * Project Folder View — Google Drive / Windows Explorer style folder workspace.
 *
 * A Project is a container of Sheets; one PDF with N pages becomes N Sheets at
 * upload time. Each sheet card reflects its real-time queue status:
 * - 'queued' / 'uploaded': Waiting in Celery FIFO queue (thumbnail locked, placeholder shown).
 * - 'processing' / 'detecting': Currently traced by worker (animated spinner, thumbnail locked).
 * - 'completed' / 'detected': Traced and ready (thumbnail loaded, clickable to enter canvas).
 * - 'error' / 'failed': Processing failed.
 *
 * Lightweight polling (every 3-4s) keeps the cards up to date automatically
 * without requiring manual browser refresh.
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
      console.error('Failed to load project or sheets:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (projectId) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Lightweight Polling (tiap 3 detik): pantau status sheet selama ada sheet yang belum selesai.
  // Saat Celery menyelesaikan Fast Trace per halaman, statusnya berubah menjadi 'completed'
  // dan thumbnail otomatis muncul serta bisa diklik tanpa perlu me-refresh halaman.
  useEffect(() => {
    const hasPending = sheets.some((s) =>
      ['uploaded', 'queued', 'processing', 'detecting'].includes(s.status)
    );
    if (!hasPending) return;

    const interval = setInterval(() => {
      void load();
    }, 3000);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheets, projectId]);

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
          ? `${created.length} halaman dipecah menjadi ${created.length} sheet antrean.`
          : `${created.length} sheet ditambahkan ke antrean.`
      );
      await load();
      window.setTimeout(() => setUploadMsg(''), 4500);
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

  const completedCount = sheets.filter(
    (s) => s.status === 'completed' || s.status === 'detected'
  ).length;
  const pendingCount = sheets.filter((s) =>
    ['uploaded', 'queued', 'processing', 'detecting'].includes(s.status)
  ).length;

  return (
    <div className="h-full flex flex-col bg-slate-50 text-slate-900 overflow-hidden">
      {/* Top Header */}
      <header className="bg-white border-b border-slate-200 px-4 lg:px-8 py-4 shadow-xs z-20 shrink-0">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center space-x-3 min-w-0">
            <Link
              href="/"
              className="p-1.5 hover:bg-slate-100 text-slate-600 rounded-lg transition shrink-0"
              title="Kembali ke Daftar Folder Project"
            >
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <div className="w-9 h-9 bg-indigo-600 rounded-xl flex items-center justify-center shrink-0 shadow-xs">
              <FolderOpen className="w-5 h-5 text-white" />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-bold tracking-tight text-slate-900 truncate">
                {project?.name || 'Loading…'}
              </h1>
              <p className="text-xs text-slate-500 truncate">
                {project?.description || 'Folder workspace P&ID drawing sheets'}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <span className="text-xs font-semibold bg-slate-100 text-slate-600 px-2.5 py-1 rounded-lg">
              {sheets.length} sheet
            </span>
            <span className="text-xs font-semibold bg-emerald-50 text-emerald-700 px-2.5 py-1 rounded-lg border border-emerald-200/60">
              {completedCount} selesai
            </span>
            {pendingCount > 0 && (
              <span className="text-xs font-semibold bg-amber-50 text-amber-700 px-2.5 py-1 rounded-lg border border-amber-200 flex items-center space-x-1.5">
                <RefreshCw className="w-3 h-3 animate-spin text-amber-600" />
                <span>{pendingCount} dalam antrean</span>
              </span>
            )}
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
              className="text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white px-3.5 py-2 rounded-lg flex items-center space-x-1.5 transition shadow-xs"
              title="Unggah PDF / PNG / JPG. PDF multi-halaman otomatis dipecah per halaman ke dalam antrean."
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
          <p className="mt-2 text-xs font-medium text-indigo-700 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-1.5 flex items-center space-x-2">
            <RefreshCw className="w-3.5 h-3.5 animate-spin text-indigo-600" />
            <span>{uploadMsg}</span>
          </p>
        )}
      </header>

      {/* Sheets Grid View */}
      <div className="flex-1 overflow-y-auto p-4 lg:p-8">
        {loading && sheets.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-slate-500 text-sm">
            <RefreshCw className="w-5 h-5 animate-spin mr-2 text-indigo-600" />
            <span>Memuat lembaran sheet…</span>
          </div>
        ) : sheets.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-center border-2 border-dashed border-slate-300 rounded-2xl bg-white p-8 max-w-lg mx-auto my-8">
            <FileImage className="w-12 h-12 text-slate-300 mb-3" />
            <p className="font-bold text-slate-800 text-sm">Belum ada sheet di folder ini</p>
            <p className="text-xs text-slate-500 mt-1 max-w-sm leading-relaxed">
              Unggah file P&amp;ID (PDF multi-halaman, PNG, atau JPG). File PDF tebal akan otomatis
              dipecah menjadi antrean sheet satu per satu agar tracing berjalan cepat dan stabil.
            </p>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="mt-4 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-xs font-semibold shadow-xs transition flex items-center space-x-1.5"
            >
              <Upload className="w-3.5 h-3.5" />
              <span>Unggah P&amp;ID Pertama</span>
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {sheets.map((s) => {
              const isQueued = s.status === 'queued' || s.status === 'uploaded';
              const isProcessing = s.status === 'processing' || s.status === 'detecting';
              const isCompleted = s.status === 'completed' || s.status === 'detected';
              const isError = s.status === 'error' || s.status === 'failed';

              return (
                <div
                  key={s.id}
                  className={`group bg-white border rounded-xl overflow-hidden shadow-2xs transition-all duration-200 flex flex-col ${
                    isCompleted
                      ? 'border-slate-200 hover:shadow-md hover:border-indigo-300'
                      : isProcessing
                      ? 'border-indigo-200 ring-1 ring-indigo-100'
                      : 'border-slate-200 opacity-95'
                  }`}
                >
                  {/* Thumbnail OR Placeholder Area */}
                  {isCompleted ? (
                    /* COMPLETED STATE: Render real thumbnail & allow click */
                    <button
                      type="button"
                      onClick={() => router.push(`/project/${projectId}/sheet/${s.id}`)}
                      className="block relative aspect-[4/3] bg-slate-900/5 overflow-hidden border-b border-slate-200 cursor-pointer w-full text-left focus:outline-none"
                      title={`Buka kanvas studio: ${s.filename}`}
                    >
                      <img
                        src={getThumbnailUrl(projectId, s.id, 480)}
                        alt={s.filename}
                        loading="lazy"
                        className="w-full h-full object-contain p-1.5 group-hover:scale-105 transition-transform duration-300"
                      />
                      <div className="absolute top-2 left-2 z-10">
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-md shadow-xs uppercase tracking-wider flex items-center space-x-1 bg-emerald-600 text-white">
                          <CheckCircle2 className="w-3 h-3" />
                          <span>Selesai</span>
                        </span>
                      </div>
                    </button>
                  ) : isProcessing ? (
                    /* PROCESSING STATE: Shimmering placeholder with spinner, NO THUMBNAIL CALL, CLICK LOCKED */
                    <div
                      className="relative aspect-[4/3] bg-slate-100/90 border-b border-slate-200 flex flex-col items-center justify-center p-4 select-none cursor-not-allowed overflow-hidden"
                      title="Sheet sedang diproses oleh worker (Fast Tracing)..."
                    >
                      {/* Pulse Shimmer Animation */}
                      <div className="absolute inset-0 bg-indigo-50/40 animate-pulse" />

                      <div className="relative z-10 w-10 h-10 rounded-full bg-white border border-indigo-200 flex items-center justify-center text-indigo-600 mb-2 shadow-xs">
                        <RefreshCw className="w-5 h-5 animate-spin text-indigo-600" />
                      </div>

                      <span className="relative z-10 text-[11px] font-bold text-indigo-700 bg-white/95 border border-indigo-200 px-2.5 py-1 rounded-full shadow-xs flex items-center space-x-1.5">
                        <RefreshCw className="w-3 h-3 animate-spin text-indigo-600" />
                        <span>Memproses...</span>
                      </span>

                      <span className="relative z-10 text-[10px] text-slate-400 mt-1.5 font-medium">
                        Fast Tracing geometri...
                      </span>
                    </div>
                  ) : isQueued ? (
                    /* QUEUED STATE: Gray placeholder with queue clock badge, NO THUMBNAIL CALL, CLICK LOCKED */
                    <div
                      className="relative aspect-[4/3] bg-slate-100/80 border-b border-slate-200 flex flex-col items-center justify-center p-4 select-none cursor-not-allowed overflow-hidden"
                      title="Sheet ini sedang dalam antrean (menunggu giliran worker)..."
                    >
                      {/* Subtle Pulse Animation */}
                      <div className="absolute inset-0 bg-slate-200/30 animate-pulse" />

                      <div className="relative z-10 w-10 h-10 rounded-full bg-white border border-amber-200 flex items-center justify-center text-amber-500 mb-2 shadow-xs">
                        <Clock className="w-5 h-5 text-amber-500 animate-pulse" />
                      </div>

                      <span className="relative z-10 text-[11px] font-bold text-amber-800 bg-white/95 border border-amber-200 px-2.5 py-1 rounded-full shadow-xs flex items-center space-x-1.5">
                        <Clock className="w-3 h-3 text-amber-600" />
                        <span>Menunggu Antrean...</span>
                      </span>

                      <span className="relative z-10 text-[10px] text-slate-400 mt-1.5 font-medium">
                        Antrean FIFO Celery
                      </span>
                    </div>
                  ) : (
                    /* ERROR STATE: Failed processing */
                    <div
                      className="relative aspect-[4/3] bg-rose-50/70 border-b border-rose-100 flex flex-col items-center justify-center p-4 select-none cursor-not-allowed"
                      title="Gagal memproses sheet ini"
                    >
                      <div className="w-10 h-10 rounded-full bg-white border border-rose-200 flex items-center justify-center text-rose-500 mb-2 shadow-xs">
                        <AlertTriangle className="w-5 h-5 text-rose-500" />
                      </div>

                      <span className="text-[11px] font-bold text-rose-700 bg-white border border-rose-200 px-2.5 py-1 rounded-full shadow-xs flex items-center space-x-1.5">
                        <AlertTriangle className="w-3 h-3 text-rose-600" />
                        <span>Gagal Tracing</span>
                      </span>

                      <span className="text-[10px] text-rose-500 mt-1.5 font-medium">
                        Error pada file
                      </span>
                    </div>
                  )}

                  {/* Card Details & Actions */}
                  <div className="p-3 flex-1 flex flex-col justify-between gap-2.5 bg-white">
                    <div className="min-w-0">
                      <p
                        className="text-xs font-bold text-slate-900 truncate"
                        title={s.filename}
                      >
                        {s.filename}
                      </p>
                      <div className="flex items-center space-x-1.5 mt-1 flex-wrap">
                        {s.sheet_number && (
                          <span className="text-[10px] font-bold bg-indigo-50 text-indigo-700 px-1.5 py-0.5 rounded border border-indigo-100">
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

                    {/* Action Row */}
                    <div className="flex items-center justify-between pt-2 border-t border-slate-100">
                      {isCompleted ? (
                        <button
                          type="button"
                          onClick={() => router.push(`/project/${projectId}/sheet/${s.id}`)}
                          className="text-[11px] font-semibold bg-slate-900 hover:bg-indigo-600 text-white px-3 py-1.5 rounded-lg flex items-center space-x-1.5 transition shadow-xs"
                        >
                          <ScanSearch className="w-3.5 h-3.5" />
                          <span>Open Studio</span>
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled
                          className="text-[11px] font-semibold bg-slate-100 text-slate-400 px-3 py-1.5 rounded-lg flex items-center space-x-1.5 cursor-not-allowed opacity-80"
                          title="Terkunci: Menunggu antrean tracing selesai sebelum masuk ke kanvas"
                        >
                          <Lock className="w-3 h-3 text-slate-400" />
                          <span>Terkunci</span>
                        </button>
                      )}

                      <button
                        type="button"
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

      {/* Delete Sheet Confirmation Modal */}
      {sheetToDelete && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 space-y-4 border border-slate-200">
            <div className="flex items-center space-x-2 text-rose-600 pb-2 border-b border-slate-100">
              <Trash2 className="w-5 h-5 text-rose-600" />
              <h3 className="font-bold text-slate-900 text-base">Hapus Sheet ini?</h3>
            </div>
            <p className="text-xs text-slate-600 leading-relaxed">
              Lembaran <strong className="text-slate-900">{sheetToDelete.filename}</strong>
              {sheetToDelete.sheet_number ? ` (Hal. ${sheetToDelete.sheet_number})` : ''} beserta
              seluruh hasil digitasinya akan dihapus permanen.
            </p>
            <div className="flex justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={() => setSheetToDelete(null)}
                className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Batal
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={deleting}
                className="px-4 py-2 text-xs font-semibold bg-rose-600 hover:bg-rose-700 disabled:bg-rose-300 text-white rounded-lg transition flex items-center space-x-1.5 shadow-xs"
              >
                {deleting && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>Hapus Sheet</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
