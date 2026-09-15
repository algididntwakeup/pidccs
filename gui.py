"""
GUI lokal — P&ID Studio.
  Fitur 1 (P&ID Digitization): deteksi simbol + piping ID + main-line (layer garis ber-dot
    yang bisa di-adjust). Klik piping ID -> pipa yang ditunjuk ter-highlight (belum diwarnai
    per corrosion system; itu Fitur 2). Koreksi manual + Undo + auto-save state.
  Fitur 2 (Corrosion Circuit): systemization & marking per process fluid — menyusul.
Jalankan:  python gui.py   (atau dobel-klik "Buka GUI.bat")
"""
import os, sys, json, copy
import numpy as np, cv2
# PENTING (Windows): torch HARUS di-import sebelum PyQt5 (kalau tidak c10.dll gagal init).
try:
    import torch  # noqa: F401
except Exception:
    pass
from PyQt5 import QtWidgets, QtGui, QtCore
try:
    from PyQt5 import sip
except Exception:
    import sip

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pidcorr import pipeline
from pidcorr import feedback
from pidcorr.systemize import systemize, run_color_map, circuitize, material_of
from pidcorr.review import build_review, apply_fluid_merge
from pidcorr.subtype import sym_class, isa_describe

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pidcache")
os.makedirs(CACHE_DIR, exist_ok=True)
COARSE_COL = {"equipment": (0, 140, 255), "instrument": (0, 180, 0), "valve": (255, 60, 0)}
PIPE_BLUE = QtGui.QColor(59, 130, 246)         # SEMUA pipa -> biru (1 warna)
PIPE_FOCUS = QtGui.QColor(29, 78, 216)         # pipa terpilih -> biru tua tebal
NEUTRAL_PID = QtGui.QColor(124, 58, 237)       # box piping ID -> VIOLET kontras (bukan abu)
PID_FILL = QtGui.QColor(124, 58, 237, 46)      # isian semi-transparan (efek stabilo)
PID_FOCUS = QtGui.QColor(217, 70, 239)         # box piping ID terpilih -> MAGENTA (paling menonjol)
PID_FOCUS_FILL = QtGui.QColor(217, 70, 239, 70)
HIGHLIGHT = QtGui.QColor(249, 115, 22)         # oranye -> DOT (handle) & preview trace

STYLE = """
* { font-family: "Segoe UI Variable", "Segoe UI", "Inter", Arial; font-size: 10pt; color: #0F172A; }
QWidget#Root { background: #EEF1F6; }

/* ---------- drawer (menu fitur, muncul via tombol ☰) ---------- */
QFrame#Drawer { background: #FFFFFF; border-right: 1px solid #E2E8F0;
                border-top-right-radius: 16px; border-bottom-right-radius: 16px; }
QWidget#Scrim { background: rgba(15, 23, 42, 90); }
QToolButton#Burger { font-size: 14pt; padding: 4px 12px; border: none; border-radius: 9px;
                     color: #334155; background: transparent; }
QToolButton#Burger:hover { background: #F1F5F9; }
QToolButton#Nav { text-align: left; padding: 13px 16px; border: none; border-radius: 10px;
                  color: #475569; font-size: 10.5pt; }
QToolButton#Nav:hover { background: #F1F5F9; }
QToolButton#Nav:checked { background: #EEF2FF; color: #4338CA; font-weight: 700; }
QLabel#Logo { font-size: 15pt; font-weight: 800; color: #4F46E5; }
QLabel#LogoSub { color: #94A3B8; font-size: 8.5pt; }

/* ---------- header & teks ---------- */
QFrame#Header { background: #FFFFFF; border-bottom: 1px solid #E2E8F0; }
QLabel#Title { font-size: 14.5pt; font-weight: 700; color: #0F172A; }
QLabel#Subtle { color: #64748B; font-size: 9pt; }
QLabel#Section { font-weight: 700; color: #334155; font-size: 9.5pt; }
QLabel#Mini { color: #94A3B8; font-size: 8pt; font-weight: 700; }
QFrame#Divider { background: #E2E8F0; border: none; }
QLabel#FileChip { background: #F1F5F9; color: #475569; border-radius: 9px;
                  padding: 3px 12px; font-size: 8.5pt; }
QLabel#SaveDirty { color: #B45309; background: #FEF3C7; border-radius: 11px;
                   padding: 5px 14px; font-weight: 600; font-size: 9pt; }
QLabel#SaveClean { color: #047857; background: #D1FAE5; border-radius: 11px;
                   padding: 5px 14px; font-weight: 600; font-size: 9pt; }

/* ---------- kartu & tombol ---------- */
QFrame#Card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 14px; }
QPushButton#Primary { background: #4F46E5; color: white; border: none; border-radius: 9px;
                      padding: 9px 20px; font-weight: 600; }
QPushButton#Primary:hover { background: #4338CA; }
QPushButton#Primary:pressed { background: #3730A3; }
QPushButton#Secondary { background: #FFFFFF; color: #334155; border: 1px solid #CBD5E1;
                        border-radius: 9px; padding: 8px 14px; font-weight: 500; }
QPushButton#Secondary:hover { background: #F8FAFC; border-color: #94A3B8; }
QPushButton#Secondary:checked { background: #D1FAE5; color: #047857; border-color: #6EE7B7;
                                font-weight: 600; }
QPushButton#Chip { background: #F1F5F9; color: #64748B; border: 1px solid transparent;
                   border-radius: 14px; padding: 5px 14px; font-size: 9pt; font-weight: 600; }
QPushButton#Chip:hover { background: #E2E8F0; }
QPushButton#Chip:checked { background: #EEF2FF; color: #4338CA; border-color: #C7D2FE; }
QPushButton#Tool { background: #FFFFFF; color: #334155; border: 1px solid #CBD5E1;
                   border-radius: 9px; padding: 8px 4px; font-size: 9pt; }
QPushButton#Tool:hover { background: #F5F7FF; border-color: #818CF8; color: #3730A3; }
/* alat yang SEDANG aktif -> terlihat jelas, dan klik lagi untuk mematikannya */
QPushButton#Tool[active="true"] { background: #4338CA; color: #FFFFFF;
                                  border: 1px solid #3730A3; font-weight: 600; }
QPushButton#Tool[active="true"]:hover { background: #3730A3; color: #FFFFFF; }
QPushButton#Tool:checked { background: #4338CA; color: #FFFFFF;
                           border: 1px solid #3730A3; font-weight: 600; }
QPushButton#Review { background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A;
                     border-radius: 9px; padding: 8px 10px; font-weight: 600; font-size: 9pt; }
QPushButton#Review:hover { background: #FEF3C7; }
QPushButton#ReviewOk { background: #ECFDF5; color: #047857; border: 1px solid #A7F3D0;
                       border-radius: 9px; padding: 8px 10px; font-weight: 600; font-size: 9pt; }
QPushButton:disabled { color: #94A3B8; background: #F1F5F9; border-color: #E2E8F0; }

/* ---------- tabel & list ---------- */
QTableWidget { border: none; background: #FFFFFF; gridline-color: #F1F5F9;
               alternate-background-color: #FAFBFD; }
QHeaderView::section { background: #F8FAFC; border: none; border-bottom: 2px solid #E2E8F0;
                       padding: 8px; color: #64748B; font-weight: 700; font-size: 8.5pt; }
QTableWidget::item { padding: 6px; }
QTableWidget::item:selected { background: #EEF2FF; color: #3730A3; }
QListWidget { border: none; background: #FFFFFF; }
QListWidget::item { padding: 9px 8px; border-radius: 8px; margin: 1px 0; }
QListWidget::item:hover { background: #F1F5F9; }
QListWidget::item:selected { background: #EEF2FF; color: #3730A3; }

/* ---------- kontrol kecil ---------- */
QCheckBox { padding: 5px 2px; color: #334155; spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1.5px solid #CBD5E1;
                       border-radius: 5px; background: #FFFFFF; }
QCheckBox::indicator:hover { border-color: #818CF8; }
QCheckBox::indicator:checked { background: #4F46E5; border-color: #4F46E5; }
QProgressBar { border: none; background: #E2E8F0; border-radius: 6px; height: 10px; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                       stop:0 #6366F1, stop:1 #8B5CF6); border-radius: 6px; }
QGraphicsView { border: none; background: #F8FAFC; border-radius: 12px; }
QStatusBar { background: #FFFFFF; border-top: 1px solid #E2E8F0; color: #64748B; font-size: 9pt; }

/* ---------- menu konteks, tooltip, scrollbar ---------- */
QMenu { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 20px; border-radius: 7px; color: #0F172A; }
QMenu::item:selected { background: #EEF2FF; color: #3730A3; }
QToolTip { background: #0F172A; color: white; border: none; padding: 6px 10px;
           border-radius: 6px; font-size: 9pt; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #CBD5E1; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #94A3B8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #CBD5E1; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #94A3B8; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


def bgr_to_qpix(img):
    rgb = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    h, w, _ = rgb.shape
    return QtGui.QPixmap.fromImage(
        QtGui.QImage(rgb.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy())


def _seg_dist(px, py, r):
    """Jarak titik ke run POLYLINE (min atas semua segmen). Run L/Z tak lagi diukur
    ke chord ujung-ke-ujung (yg bikin klik di lengan belok meleset)."""
    pts = r.get("points")
    if not pts or len(pts) < 2:
        pts = [[r["x1"], r["y1"]], [r["x2"], r["y2"]]]
    best = 1e18
    for a, b in zip(pts, pts[1:]):
        x1, y1, x2, y2 = a[0], a[1], b[0], b[1]
        vx, vy = x2 - x1, y2 - y1
        L2 = vx * vx + vy * vy or 1
        t = max(0, min(1, ((px - x1) * vx + (py - y1) * vy) / L2))
        d = ((px - (x1 + t * vx)) ** 2 + (py - (y1 + t * vy)) ** 2) ** 0.5
        if d < best:
            best = d
    return best


# ------------------------------------------------------ worker --------------------
class DetectWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str); done = QtCore.pyqtSignal(dict); failed = QtCore.pyqtSignal(str)

    def __init__(self, img, path, dpi, pids_cache, rot=0):
        super().__init__(); self.img, self.path, self.dpi, self.pids_cache = img, path, dpi, pids_cache
        self.rot = rot

    def run(self):
        try:
            res = pipeline.run_pipeline(self.img, self.path, self.dpi, self.rot,
                                        pids_cache=self.pids_cache, progress=self.progress.emit)
            self.done.emit(res)
        except Exception as e:
            import traceback; traceback.print_exc(); self.failed.emit(repr(e))


# ------------------------------------------------------ loading overlay -----------
class LoadingOverlay(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(243,244,246,0.82);")
        lay = QtWidgets.QVBoxLayout(self); lay.setAlignment(QtCore.Qt.AlignCenter)
        card = QtWidgets.QFrame(); card.setObjectName("Card"); card.setFixedWidth(380)
        cl = QtWidgets.QVBoxLayout(card); cl.setContentsMargins(26, 26, 26, 26); cl.setSpacing(12)
        t = QtWidgets.QLabel("Processing P&ID…"); t.setObjectName("Title"); t.setAlignment(QtCore.Qt.AlignCenter)
        self.sub = QtWidgets.QLabel("preparing…"); self.sub.setObjectName("Subtle")
        self.sub.setAlignment(QtCore.Qt.AlignCenter); self.sub.setWordWrap(True)
        bar = QtWidgets.QProgressBar(); bar.setRange(0, 0); bar.setTextVisible(False)
        cl.addWidget(t); cl.addWidget(self.sub); cl.addWidget(bar); lay.addWidget(card); self.hide()

    def set_status(self, t): self.sub.setText(t)

    def show_over(self):
        if self.parent(): self.setGeometry(self.parent().rect())
        self.raise_(); self.show()


# ------------------------------------------------------ draggable dot -------------
class Handle(QtWidgets.QGraphicsEllipseItem):
    R = 8

    def __init__(self, x, y, on_start, on_move):
        super().__init__(-self.R, -self.R, 2 * self.R, 2 * self.R)
        self.setPos(x, y)
        self.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
        self.setPen(QtGui.QPen(HIGHLIGHT, 3))
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsMovable |
                      QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(20); self.setCursor(QtCore.Qt.SizeAllCursor)
        self._on_start, self._on_move = on_start, on_move

    def mousePressEvent(self, e):
        self._on_start(); super().mousePressEvent(e)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            self._on_move(self.pos())
        return super().itemChange(change, value)


# ------------------------------------------------------ view ----------------------
class View(QtWidgets.QGraphicsView):
    clicked = QtCore.pyqtSignal(QtCore.QPointF); doubleClicked = QtCore.pyqtSignal(QtCore.QPointF)
    rightClicked = QtCore.pyqtSignal(QtCore.QPointF)
    banded = QtCore.pyqtSignal(QtCore.QRectF)          # rubber-band selesai (mode band_on)

    def __init__(self, scene):
        super().__init__(scene)
        # NAVIGASI: geser = scroll 2 jari (trackpad), zoom = Ctrl+scroll. TIDAK pakai klik-hold.
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        self.band_on = False                            # True -> klik-tahan-drag = kotak
        self._band = None; self._band_origin = None

    def wheelEvent(self, e):
        if e.modifiers() & QtCore.Qt.ControlModifier:            # Ctrl+scroll -> zoom
            s = 1.25 if e.angleDelta().y() > 0 else 0.8; self.scale(s, s)
        else:
            super().wheelEvent(e)                                # scroll 2 jari -> geser gambar

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.band_on:
            # mode rubber-band: klik-tahan-drag membentuk kotak (utk posisi piping ID)
            self._band_origin = e.pos()
            if self._band is None:
                self._band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self.viewport())
            self._band.setGeometry(QtCore.QRect(e.pos(), QtCore.QSize()))
            self._band.show()
        elif e.button() == QtCore.Qt.LeftButton:
            if isinstance(self.itemAt(e.pos()), Handle):
                super().mousePressEvent(e)                 # klik DOT -> perlu super utk drag
            else:
                # klik aksi: JANGAN super (aksi bisa clear scene -> super proses item terhapus -> crash)
                self.clicked.emit(self.mapToScene(e.pos()))
        elif e.button() == QtCore.Qt.RightButton:
            self.rightClicked.emit(self.mapToScene(e.pos()))   # menu konteks (hapus tracing)
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.band_on and self._band_origin is not None:
            self._band.setGeometry(QtCore.QRect(self._band_origin, e.pos()).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.band_on and self._band_origin is not None:
            r = QtCore.QRect(self._band_origin, e.pos()).normalized()
            self._band.hide(); self._band_origin = None
            if r.width() >= 6 and r.height() >= 6:         # drag -> kotak
                self.banded.emit(self.mapToScene(r).boundingRect())
            else:                                          # klik biasa -> titik (diabaikan mode band)
                self.clicked.emit(self.mapToScene(e.pos()))
            return
        super().mouseReleaseEvent(e)

    def stop_band(self):
        self.band_on = False; self._band_origin = None
        if self._band is not None:
            self._band.hide()

    def mouseDoubleClickEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and not isinstance(self.itemAt(e.pos()), Handle):
            self.doubleClicked.emit(self.mapToScene(e.pos()))
        else:
            super().mouseDoubleClickEvent(e)


# ------------------------------------------------------ main window ---------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("P&ID Studio"); self.resize(1560, 960); self.setStyleSheet(STYLE)
        self.result = None; self.img_bgr = None; self.image_path = ""; self.rot = 0
        self.pick_mode = None; self._active_tool = None; self.sel_row = -1; self.layers = {}
        self.trace_pts = []; self.trace_items = []; self.handles = []
        self.box_edit = False; self.box_sel = None; self._edit_items = []   # edit kotak simbol/pid
        self.undo_stack = []; self.dirty = False; self._focus_run = None; self._focus_line = None
        self.sym_filter = set()                   # subtype simbol yang disorot (chip aktif)
        self.sym_filter_coarse = set()            # kelas induk tersorot (Valve/Instrument/Equipment)

        root = QtWidgets.QWidget(); root.setObjectName("Root")
        h = QtWidgets.QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._detection_page())       # 0 — Fitur 1
        self.stack.addWidget(self._system_page())          # 1 — Fitur 2a Corrosion System
        self.stack.addWidget(self._circuit_page())         # 2 — Fitur 2b Corrosion Circuit
        h.addWidget(self.stack, 1); self.setCentralWidget(root)
        self._build_drawer(root)                           # menu fitur = drawer overlay (tombol ☰)

        self.overlay = LoadingOverlay(self.det_page)
        QtWidgets.QShortcut(QtGui.QKeySequence.Undo, self, self.undo)   # Ctrl+Z
        QtWidgets.QShortcut(QtGui.QKeySequence.Save, self, self.save)   # Ctrl+S
        QtWidgets.QShortcut(QtGui.QKeySequence("Escape"), self, self._cancel)
        self._set_dirty(False)
        self.statusBar().showMessage("Ready. Open a P&ID to begin.")

    # ---------- drawer (menu fitur; muncul via tombol ☰, kanvas tetap full lebar) ----------
    def _build_drawer(self, parent):
        self.scrim = QtWidgets.QWidget(parent); self.scrim.setObjectName("Scrim")
        self.scrim.hide()
        self.scrim.mousePressEvent = lambda e: self._close_drawer()   # klik luar -> tutup
        self.drawer = QtWidgets.QFrame(parent); self.drawer.setObjectName("Drawer")
        self.drawer.setFixedWidth(272)
        v = QtWidgets.QVBoxLayout(self.drawer); v.setContentsMargins(16, 22, 16, 16); v.setSpacing(6)
        v.addWidget(self._lbl("◆ P&ID Studio", "Logo"))
        v.addWidget(self._lbl("Digitization & Corrosion CCD", "LogoSub")); v.addSpacing(18)
        self.nav_btns = []
        for text, idx in [("\U0001F50D  P&ID Digitization", 0),
                          ("\U0001F3A8  Corrosion System", 1),
                          ("\U0001F517  Corrosion Circuit", 2)]:
            b = QtWidgets.QToolButton(); b.setObjectName("Nav"); b.setText(text)
            b.setCheckable(True); b.setChecked(idx == 0); b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.clicked.connect(lambda _, i=idx: self._nav(i)); v.addWidget(b); self.nav_btns.append(b)
        v.addStretch(1); v.addWidget(self._lbl("v2 · local", "LogoSub"))
        sh = QtWidgets.QGraphicsDropShadowEffect(blurRadius=34, xOffset=3, yOffset=0)
        sh.setColor(QtGui.QColor(15, 23, 42, 80)); self.drawer.setGraphicsEffect(sh)
        self.drawer.hide()
        self._drawer_anim = QtCore.QPropertyAnimation(self.drawer, b"pos", self)
        self._drawer_anim.setDuration(170)
        self._drawer_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

    def _burger(self):
        b = QtWidgets.QToolButton(); b.setObjectName("Burger"); b.setText("☰")
        b.setToolTip("Feature menu — Digitization / Corrosion System / Corrosion Circuit")
        b.setCursor(QtCore.Qt.PointingHandCursor); b.clicked.connect(self._toggle_drawer)
        return b

    def _toggle_drawer(self):
        self._close_drawer() if self.drawer.isVisible() else self._open_drawer()

    def _open_drawer(self):
        par = self.centralWidget()
        self.scrim.setGeometry(0, 0, par.width(), par.height())
        self.scrim.show(); self.scrim.raise_()
        self.drawer.setFixedHeight(par.height())
        self.drawer.move(-self.drawer.width(), 0)
        self.drawer.show(); self.drawer.raise_()
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(QtCore.QPoint(-self.drawer.width(), 0))
        self._drawer_anim.setEndValue(QtCore.QPoint(0, 0))
        self._drawer_anim.start()

    def _close_drawer(self):
        self.scrim.hide(); self.drawer.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "drawer") and self.drawer.isVisible():
            par = self.centralWidget()
            self.scrim.setGeometry(0, 0, par.width(), par.height())
            self.drawer.setFixedHeight(par.height())

    def _lbl(self, text, obj):
        lbl = QtWidgets.QLabel(text); lbl.setObjectName(obj); return lbl

    def _nav(self, idx):
        for i, b in enumerate(self.nav_btns): b.setChecked(i == idx)
        self.stack.setCurrentIndex(idx)
        self._close_drawer()
        if idx == 1:                          # masuk Corrosion System -> auto-markup
            self.render_system(fit=True)
        elif idx == 2:                        # masuk Corrosion Circuit -> auto-markup
            self.render_circuit(fit=True)

    # ---------- Fitur 2b: Corrosion Circuit page ----------
    def _circuit_page(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        head = QtWidgets.QFrame(); head.setObjectName("Header"); head.setFixedHeight(66)
        hl = QtWidgets.QHBoxLayout(head); hl.setContentsMargins(14, 10, 24, 10)
        hl.addWidget(self._burger()); hl.addSpacing(8)
        tcol = QtWidgets.QVBoxLayout(); tcol.setSpacing(0)
        tcol.addWidget(self._lbl("Corrosion Circuit", "Title"))
        tcol.addWidget(self._lbl("systems split by the piping-class material — API RP 970 §5.6", "Subtle"))
        hl.addLayout(tcol); hl.addStretch(1)
        self.cir_count = self._lbl("", "SaveClean"); hl.addWidget(self.cir_count); hl.addSpacing(10)
        hl.addWidget(self._fit_btn(lambda: self._fit_view(self.cir_view, self.cir_scene)))
        b = QtWidgets.QPushButton("⬇  Export…"); b.setObjectName("Secondary")
        b.setCursor(QtCore.Qt.PointingHandCursor); b.setToolTip(
            "Export the line register to Excel or the marking image to PNG (optional, not automatic)")
        b.clicked.connect(lambda: self._export_menu("circuit")); hl.addWidget(b)
        outer.addWidget(head)

        body = QtWidgets.QHBoxLayout(); body.setContentsMargins(18, 18, 18, 18); body.setSpacing(16)
        card = QtWidgets.QFrame(); card.setObjectName("Card")
        cl = QtWidgets.QVBoxLayout(card); cl.setContentsMargins(8, 8, 8, 8)
        self.cir_scene = QtWidgets.QGraphicsScene(); self.cir_view = View(self.cir_scene)
        self.cir_view.clicked.connect(self.on_circuit_canvas_click)
        cl.addWidget(self.cir_view)
        body.addWidget(card, 1)

        panel = QtWidgets.QFrame(); panel.setObjectName("Card"); panel.setFixedWidth(430)
        pv = QtWidgets.QVBoxLayout(panel); pv.setContentsMargins(16, 16, 16, 16); pv.setSpacing(8)
        pv.addWidget(self._lbl("Corrosion Circuit Table", "Section"))
        pv.addWidget(self._lbl("click a system / circuit row → isolate its marking", "Subtle"))
        self.cir_table = QtWidgets.QTableWidget(0, 3)
        self.cir_table.setHorizontalHeaderLabels(["Piping ID", "Piping Class", "Material"])
        self.cir_table.horizontalHeader().setStretchLastSection(True)
        self.cir_table.setColumnWidth(0, 190); self.cir_table.setColumnWidth(1, 100)
        self.cir_table.verticalHeader().setVisible(False)
        self.cir_table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.cir_table.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        self.cir_table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.cir_table.setShowGrid(False)
        self.cir_table.cellClicked.connect(self.on_circuit_row)
        pv.addWidget(self.cir_table, 1)
        self.btn_cir_all = QtWidgets.QPushButton("Show all circuits")
        self.btn_cir_all.setObjectName("Secondary"); self.btn_cir_all.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_cir_all.clicked.connect(lambda: self.render_circuit(fit=False))
        pv.addWidget(self.btn_cir_all)
        pv.addWidget(self._lbl("Material is taken from the piping-class code (2nd letter) — an assumption; replace it with the company's own mapping library.", "Subtle"))
        body.addWidget(panel)
        outer.addLayout(body, 1)
        return page

    def render_circuit(self, selected=None, fit=False):
        """Warnai pipa per CIRCUIT. selected: ('sys', si) isolasi 1 system penuh,
        ('cir', si, ci) isolasi 1 circuit. System 1-material -> warna = warna system."""
        if not hasattr(self, "cir_scene"):
            return
        self.cir_scene.clear(); self.cir_table.setRowCount(0)
        if self.result is None or self.img_bgr is None:
            self.cir_count.setText("")
            self.cir_scene.addText("Run 'Detect' in P&ID Digitization first.")
            return
        self.cir_scene.addPixmap(bgr_to_qpix(self.img_bgr))
        self.cir_scene.setSceneRect(QtCore.QRectF(self.cir_scene.itemsBoundingRect()))
        systems = circuitize(self.result); self.cir_systems = systems
        runs = self.result["runs"]

        def dim(si, ci):
            if selected is None:
                return False
            if selected[0] == "sys":
                return selected[1] != si
            return (selected[1], selected[2]) != (si, ci)

        for si, s in enumerate(systems):
            for ci, c in enumerate(s["circuits"]):
                pen = QtGui.QPen(QtGui.QColor(205, 210, 220) if dim(si, ci)
                                 else QtGui.QColor(*c["color"]), 2 if dim(si, ci) else 6)
                for ri in c["run_idxs"]:
                    if 0 <= ri < len(runs):
                        it = QtWidgets.QGraphicsPathItem(self._path(self._run_verts(runs[ri])))
                        it.setPen(pen); self.cir_scene.addItem(it)
        n_cp = self._draw_conn_points(self.cir_scene)
        self._fill_circuit_table(systems)
        n_cir = sum(len(s["circuits"]) for s in systems)
        self.cir_count.setText(f"{len(systems)} system · {n_cir} circuit"
                               + (f" · {n_cp} spec break" if n_cp else ""))
        if fit:
            self.cir_view.fitInView(self.cir_scene.sceneRect(), QtCore.Qt.KeepAspectRatio)

    def _fill_circuit_table(self, systems):
        """Tabel bertingkat: baris SYSTEM (blok warna) -> baris CIRCUIT (blok warna
        circuit) -> baris piping ID (pid | class | material)."""
        t = self.cir_table; t.blockSignals(True); t.setRowCount(0)
        self._cir_rowmap = []
        bold = QtGui.QFont(); bold.setBold(True)
        pids = self.result["piping_ids"]

        def _swatch(color):
            pix = QtGui.QPixmap(14, 14); pix.fill(QtGui.QColor(*color))
            return QtGui.QIcon(pix)

        for si, s in enumerate(systems):
            row = t.rowCount(); t.insertRow(row); t.setSpan(row, 0, 1, 3)
            it = QtWidgets.QTableWidgetItem(_swatch(s["color"]),
                                            f" Corrosion System #{s['index']:02d} — {s['fluid']}")
            it.setFont(bold); it.setBackground(QtGui.QColor("#F1F5F9"))
            t.setItem(row, 0, it); self._cir_rowmap.append(("sys", si, None))
            for ci, c in enumerate(s["circuits"]):
                row = t.rowCount(); t.insertRow(row); t.setSpan(row, 0, 1, 3)
                mat = c["material"] or "—"
                it = QtWidgets.QTableWidgetItem(_swatch(c["color"]),
                                                f"   Circuit {c['code']}  ·  material {mat}")
                it.setBackground(QtGui.QColor("#F8FAFC"))
                t.setItem(row, 0, it); self._cir_rowmap.append(("cir", si, ci))
                for pi in c["pid_idxs"]:
                    p = pids[pi]
                    row = t.rowCount(); t.insertRow(row)
                    for col, val in enumerate([p["pid"], p.get("pclass", ""),
                                               material_of(p.get("pclass"))]):
                        t.setItem(row, col, QtWidgets.QTableWidgetItem(str(val)))
                    self._cir_rowmap.append(("pid", si, ci))
        t.blockSignals(False)

    def on_circuit_row(self, row, col):
        if not getattr(self, "_cir_rowmap", None) or row >= len(self._cir_rowmap):
            return
        kind, si, ci = self._cir_rowmap[row]
        if kind == "sys":
            self.render_circuit(selected=("sys", si))
            self.statusBar().showMessage(f"isolating system #{si + 1:02d}")
        else:
            self.render_circuit(selected=("cir", si, ci))
            c = self.cir_systems[si]["circuits"][ci]
            self.statusBar().showMessage(f"isolating circuit {c['code']} (material {c['material'] or '—'})")

    # ---------- export (interaktif — hanya saat user minta) ----------
    def _export_menu(self, mode):
        if self.result is None or self.img_bgr is None:
            return self.statusBar().showMessage("no result yet — open a P&ID and run Detect first.")
        menu = QtWidgets.QMenu(self)
        a_doc = menu.addAction("\U0001F4DD  Asset register — Word (.docx)")
        menu.addSeparator()
        a_xls = menu.addAction("\U0001F4CA  Register Excel (.xlsx)")
        a_png = menu.addAction("\U0001F5BC  Marking image + legend (.png)")
        a_pdf = menu.addAction("\U0001F4C4  Marking PDF — editable in Acrobat (.pdf)")
        act = menu.exec_(QtGui.QCursor.pos())
        if act is None:
            return
        stem = os.path.splitext(os.path.basename(self.image_path))[0] or "pid"
        if act == a_doc:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save asset register", stem + "_asset_register.docx", "Word (*.docx)")
            if not path:
                return
            try:
                from pidcorr.asset_register import export_asset_register_docx
                self.overlay.set_status("reading asset tags…"); self.overlay.show_over()
                QtWidgets.QApplication.processEvents()
                try:
                    s = export_asset_register_docx(
                        self.result, path, drawing_name=stem, img_bgr=self.img_bgr,
                        progress=lambda m: (self.overlay.set_status(m),
                                            QtWidgets.QApplication.processEvents()))
                finally:
                    self.overlay.hide()
                self.statusBar().showMessage(
                    f"✓ asset register exported → {path}  ·  "
                    f"{s['Equipment']} equipment · {s['Instrument']} instrument · "
                    f"{s['Valve']} valve · {s['Piping line (line number)']} line")
            except Exception as e:
                self.overlay.hide()
                QtWidgets.QMessageBox.critical(self, "Export failed",
                                               f"Asset register export failed:\n{e}")
        elif act == a_xls:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save line register", stem + "_register.xlsx", "Excel (*.xlsx)")
            if not path:
                return
            try:
                from pidcorr.export import export_register_xlsx
                n = export_register_xlsx(self.result, path, drawing_name=stem)
                self.statusBar().showMessage(f"✓ register {n} lines exported → {path}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Export failed", f"Excel export failed:\n{e}")
        elif act == a_png:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save marking image", f"{stem}_{mode}.png", "PNG (*.png)")
            if not path:
                return
            try:
                from pidcorr.export import render_marked_png
                vis = render_marked_png(self.img_bgr, self.result, mode=mode)
                ok, buf = cv2.imencode(".png", vis)      # imencode: aman utk path non-ASCII Windows
                if not ok:
                    raise RuntimeError("PNG encoding failed")
                buf.tofile(path)
                self.statusBar().showMessage(f"✓ marking image exported → {path}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Export failed", f"PNG export failed:\n{e}")
        elif act == a_pdf:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save marking PDF (editable in Acrobat)", f"{stem}_{mode}.pdf",
                "PDF (*.pdf)")
            if not path:
                return
            try:
                from pidcorr.export import export_marked_pdf
                n = export_marked_pdf(self.result, path, mode=mode)
                self.statusBar().showMessage(
                    f"✓ PDF with {n} marking annotations exported → {path} "
                    "(open in Acrobat: each line can be selected / edited / deleted)")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Export failed", f"PDF export failed:\n{e}")

    # ---------- filter simbol per kelas (induk + subtype) ----------
    def _sym_visible(self, s):
        """Simbol tampil? Tanpa filter -> semua. Dgn filter -> UNION: kelas induk aktif
        (mis. 'Semua Valve' = seluruh valve apa pun subtypenya) ATAU subtype aktif."""
        if not self.sym_filter and not self.sym_filter_coarse:
            return True
        return s.get("coarse") in self.sym_filter_coarse or sym_class(s) in self.sym_filter

    def _rebuild_symbol_chips(self):
        """Bangun ulang chip induk (Semua Valve · 37, ...) + chip subtype (PSV · 4, ...)."""
        if not hasattr(self, "sym_chip_grid"):
            return
        from collections import Counter
        syms = (self.result or {}).get("symbols", [])
        counts = Counter(sym_class(s) for s in syms)
        coarse_counts = Counter(s.get("coarse") for s in syms)
        self.sym_filter &= set(counts)
        self.sym_filter_coarse &= set(coarse_counts)
        # --- chip induk ---
        while self.sym_parent_row.count():
            it = self.sym_parent_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.sym_parent_chips = {}
        for co, label in (("valve", "All Valves"), ("instrument", "All Instruments"),
                          ("equipment", "All Equipment")):
            n = coarse_counts.get(co, 0)
            if n == 0:
                continue
            b = QtWidgets.QPushButton(f"{label} · {n}"); b.setObjectName("Chip")
            b.setCheckable(True); b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setToolTip(f"Highlight ALL {co} ({n} points) of any type — then narrow it down "
                         "with the type chips below if needed.")
            b.blockSignals(True); b.setChecked(co in self.sym_filter_coarse); b.blockSignals(False)
            b.toggled.connect(lambda on, c=co: self._on_sym_parent(c, on))
            self.sym_parent_row.addWidget(b)
            self.sym_parent_chips[co] = b
        self.sym_parent_row.addStretch(1)
        # --- chip subtype ---
        while self.sym_chip_grid.count():
            it = self.sym_chip_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.sym_chips = {}
        for i, (name, n) in enumerate(sorted(counts.items(), key=lambda x: (-x[1], x[0]))):
            b = QtWidgets.QPushButton(f"{name} · {n}"); b.setObjectName("Chip")
            b.setCheckable(True); b.setCursor(QtCore.Qt.PointingHandCursor)
            tip = isa_describe(name) if name.isupper() and len(name) <= 4 else name
            b.setToolTip(f"{tip} — {n} points. Click to highlight this type on the drawing.")
            b.blockSignals(True); b.setChecked(name in self.sym_filter); b.blockSignals(False)
            b.toggled.connect(lambda on, nm=name: self._on_sym_chip(nm, on))
            self.sym_chip_grid.addWidget(b, i // 2, i % 2)
            self.sym_chips[name] = b
        self._update_filter_badge()

    def _sym_status(self):
        n = sum(1 for s in (self.result or {}).get("symbols", []) if self._sym_visible(s))
        if self.sym_filter or self.sym_filter_coarse:
            aktif = [f"all {c}" for c in sorted(self.sym_filter_coarse)] + sorted(self.sym_filter)
            self.statusBar().showMessage(f"highlight: {', '.join(aktif)} — {n} points.")
        else:
            self.statusBar().showMessage("symbol filter off — all symbols shown.")

    def _on_sym_parent(self, coarse, on):
        (self.sym_filter_coarse.add if on else self.sym_filter_coarse.discard)(coarse)
        self.refresh(); self._sym_status(); self._update_filter_badge()

    def _on_sym_chip(self, name, on):
        (self.sym_filter.add if on else self.sym_filter.discard)(name)
        self.refresh(); self._sym_status(); self._update_filter_badge()

    # ---------- util UX kecil ----------
    def _fit_btn(self, fn):
        b = QtWidgets.QPushButton("⤢ Fit"); b.setObjectName("Secondary")
        b.setToolTip("Show the whole drawing (fit to window)")
        b.setCursor(QtCore.Qt.PointingHandCursor); b.clicked.connect(fn)
        return b

    def _fit_view(self, view, scene):
        if scene.sceneRect().isValid():
            view.fitInView(scene.sceneRect(), QtCore.Qt.KeepAspectRatio)

    def _toggle_reviewed(self):
        if self.result is None:
            self.btn_reviewed.setChecked(False)
            return self.statusBar().showMessage("no result yet — run Detect first.")
        self.result["reviewed"] = self.btn_reviewed.isChecked()
        self._set_dirty(True)
        self.statusBar().showMessage("marked as REVIEWED — remember to Save (Ctrl+S)."
                                     if self.result["reviewed"] else "review flag cleared.")

    # ---------- panel "Perlu Review" ----------
    _REV_ICON = {"fluid_suspect": "\U0001F9EA", "pid_none": "\U0001F517",
                 "fluid_empty": "❓", "orphan_run": "➖", "pid_in_equipment": "\U0001F3ED",
                 "cp_unattached": "✂"}

    def open_review(self):
        if self.result is None:
            return self.statusBar().showMessage("no result yet — open a P&ID and run Detect first.")
        if getattr(self, "_rev_dlg", None) is None:
            d = QtWidgets.QDialog(self); d.setWindowTitle("Needs Review — inspection priority")
            d.setModal(False); d.resize(600, 500)
            v = QtWidgets.QVBoxLayout(d); v.setContentsMargins(14, 14, 14, 14); v.setSpacing(8)
            lbl = self._lbl("Click an item → focus on that element in the canvas. DOUBLE-CLICK a fluid-merge suggestion to apply it (with confirmation).", "Subtle")
            lbl.setWordWrap(True); v.addWidget(lbl)
            self.rev_list = QtWidgets.QListWidget(); v.addWidget(self.rev_list, 1)
            self.rev_list.itemClicked.connect(self._review_click)
            self.rev_list.itemDoubleClicked.connect(self._review_dblclick)
            b = QtWidgets.QPushButton("Reload list"); b.setObjectName("Secondary")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(self._refresh_review); v.addWidget(b)
            self._rev_dlg = d
        self._refresh_review()
        self._rev_dlg.show(); self._rev_dlg.raise_()

    def _refresh_review(self):
        self._rev_issues = build_review(self.result) if self.result else []
        if getattr(self, "_rev_dlg", None) is not None:
            self.rev_list.clear()
            for x in self._rev_issues:
                self.rev_list.addItem(f"{self._REV_ICON.get(x['kind'], '•')}  {x['msg']}")
        if hasattr(self, "btn_review"):
            n = len(self._rev_issues)
            self.btn_review.setText(f"⚠ Review ({n})" if n else "✓ Review clear")
            self.btn_review.setObjectName("Review" if n else "ReviewOk")
            st = self.btn_review.style()
            st.unpolish(self.btn_review); st.polish(self.btn_review)   # terapkan gaya baru

    def _review_issue(self, item):
        row = self.rev_list.row(item)
        return self._rev_issues[row] if 0 <= row < len(self._rev_issues) else None

    def _review_click(self, item):
        x = self._review_issue(item)
        if not x:
            return
        if x["kind"] in ("pid_none", "fluid_empty"):
            self._nav(0); self.table.selectRow(x["pid_idx"])
        elif x["kind"] == "orphan_run":
            self._nav(0); self._flash_run(x["run_idx"])
        elif x["kind"] == "fluid_suspect":
            self.statusBar().showMessage(
                f"suggestion: merge '{x['fluid']}' → '{x['suggest']}' — double-click to apply.")

    def _review_dblclick(self, item):
        x = self._review_issue(item)
        if not x:
            return
        if x["kind"] != "fluid_suspect":
            return self._review_click(item)
        ok = QtWidgets.QMessageBox.question(
            self, "Merge fluid code",
            f"Change fluid '{x['fluid']}' to '{x['suggest']}' on {len(x['pid_idxs'])} line?\n"
            "(likely an OCR misread; you can undo with Ctrl+Z)",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if ok == QtWidgets.QMessageBox.Yes:
            self._snapshot()
            n = apply_fluid_merge(self.result, x)
            self._set_dirty(True); self.refresh(); self._refresh_review()
            self.statusBar().showMessage(f"✓ {n} lines merged into fluid '{x['suggest']}'.")

    def _flash_run(self, ri):
        """Sorot 1 run di kanvas digitisasi (utk orphan review) + center ke sana."""
        runs = self.result.get("runs", [])
        if not (0 <= ri < len(runs)):
            return
        self.show_all()
        verts = self._run_verts(runs[ri])
        it = QtWidgets.QGraphicsPathItem(self._path(verts))
        it.setPen(QtGui.QPen(HIGHLIGHT, 9)); self.scene.addItem(it)
        mx, my = verts[len(verts) // 2]
        self.view.centerOn(mx, my)

    def on_circuit_canvas_click(self, pt):
        if self.result is None or not getattr(self, "cir_systems", None):
            return
        runs = self.result["runs"]; best = (1e9, None)
        for si, s in enumerate(self.cir_systems):
            for ci, c in enumerate(s["circuits"]):
                for ri in c["run_idxs"]:
                    if 0 <= ri < len(runs):
                        d = _seg_dist(pt.x(), pt.y(), runs[ri])
                        if d < best[0]:
                            best = (d, (si, ci))
        if best[1] and best[0] <= 30:
            si, ci = best[1]
            self.render_circuit(selected=("cir", si, ci))
            c = self.cir_systems[si]["circuits"][ci]
            self.statusBar().showMessage(f"isolating circuit {c['code']} (material {c['material'] or '—'})")

    # ---------- Fitur 2a: Corrosion System page ----------
    def _system_page(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        head = QtWidgets.QFrame(); head.setObjectName("Header"); head.setFixedHeight(66)
        hl = QtWidgets.QHBoxLayout(head); hl.setContentsMargins(14, 10, 24, 10)
        hl.addWidget(self._burger()); hl.addSpacing(8)
        tcol = QtWidgets.QVBoxLayout(); tcol.setSpacing(0)
        tcol.addWidget(self._lbl("Corrosion System", "Title"))
        tcol.addWidget(self._lbl("pipes marked by process fluid — API RP 970 §5.6", "Subtle"))
        hl.addLayout(tcol); hl.addStretch(1)
        self.sys_count = self._lbl("", "SaveClean"); hl.addWidget(self.sys_count); hl.addSpacing(10)
        hl.addWidget(self._fit_btn(lambda: self._fit_view(self.sys_view, self.sys_scene)))
        b = QtWidgets.QPushButton("⬇  Export…"); b.setObjectName("Secondary")
        b.setCursor(QtCore.Qt.PointingHandCursor); b.setToolTip(
            "Export the line register to Excel or the marking image to PNG (optional, not automatic)")
        b.clicked.connect(lambda: self._export_menu("system")); hl.addWidget(b)
        outer.addWidget(head)

        body = QtWidgets.QHBoxLayout(); body.setContentsMargins(18, 18, 18, 18); body.setSpacing(16)
        card = QtWidgets.QFrame(); card.setObjectName("Card")
        cl = QtWidgets.QVBoxLayout(card); cl.setContentsMargins(8, 8, 8, 8)
        self.sys_scene = QtWidgets.QGraphicsScene(); self.sys_view = View(self.sys_scene)
        self.sys_view.clicked.connect(self.on_system_canvas_click)
        cl.addWidget(self.sys_view)
        body.addWidget(card, 1)

        panel = QtWidgets.QFrame(); panel.setObjectName("Card"); panel.setFixedWidth(300)
        pv = QtWidgets.QVBoxLayout(panel); pv.setContentsMargins(16, 16, 16, 16); pv.setSpacing(8)
        pv.addWidget(self._lbl("Corrosion System List", "Section"))
        pv.addWidget(self._lbl("click → isolate that system on the drawing", "Subtle"))
        self.sys_list = QtWidgets.QListWidget(); self.sys_list.itemClicked.connect(self.on_system_select)
        pv.addWidget(self.sys_list, 1)
        pv.addWidget(self._lbl("Members of the selected system", "Section"))
        self.sys_members = QtWidgets.QTableWidget(0, 2)
        self.sys_members.setHorizontalHeaderLabels(["Line No", "Class"])
        self.sys_members.horizontalHeader().setStretchLastSection(True)
        self.sys_members.setColumnWidth(0, 185)
        self.sys_members.verticalHeader().setVisible(False)
        self.sys_members.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.sys_members.setShowGrid(False)
        pv.addWidget(self.sys_members, 1)
        self.btn_sys_all = QtWidgets.QPushButton("Show all systems")
        self.btn_sys_all.setObjectName("Secondary"); self.btn_sys_all.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_sys_all.clicked.connect(lambda: self.render_system(fit=False))
        pv.addWidget(self.btn_sys_all)
        self.sys_hint = self._lbl("No detection yet. Open a P&ID, then click 'Detect' in P&ID Digitization.", "Subtle")
        self.sys_hint.setWordWrap(True); pv.addWidget(self.sys_hint)
        body.addWidget(panel)
        outer.addLayout(body, 1)
        return page

    def render_system(self, selected=None, fit=False):
        """Warnai pipa per corrosion system (process fluid). selected=fluid -> isolasi."""
        if not hasattr(self, "sys_scene"):
            return
        self.sys_scene.clear(); self.sys_list.clear()
        if self.result is None or self.img_bgr is None:
            self.sys_count.setText(""); self.sys_hint.show()
            self.sys_scene.addText("Run 'Detect' in P&ID Digitization first.")
            return
        self.sys_hint.hide()
        self.sys_scene.addPixmap(bgr_to_qpix(self.img_bgr))
        self.sys_scene.setSceneRect(QtCore.QRectF(self.sys_scene.itemsBoundingRect()))
        systems = systemize(self.result); self.systems = systems
        runs = self.result["runs"]
        for s in systems:
            dim = selected is not None and s["fluid"] != selected
            pen = QtGui.QPen(QtGui.QColor(205, 210, 220) if dim else QtGui.QColor(*s["color"]),
                             2 if dim else 6)
            for ri in s["run_idxs"]:
                if 0 <= ri < len(runs):
                    it = QtWidgets.QGraphicsPathItem(self._path(self._run_verts(runs[ri])))
                    it.setPen(pen); self.sys_scene.addItem(it)
        n_cp = self._draw_conn_points(self.sys_scene)
        self._fill_system_legend(systems, selected)
        self._fill_system_members(systems, selected)
        n_pipe = sum(s["n_pipes"] for s in systems)
        self.sys_count.setText(f"{len(systems)} system · {n_pipe} pipes coloured"
                               + (f" · {n_cp} spec break" if n_cp else ""))
        if fit:
            self.sys_view.fitInView(self.sys_scene.sceneRect(), QtCore.Qt.KeepAspectRatio)

    def _draw_conn_points(self, scene):
        """Gambar penanda CONNECTION POINT (spec break) di atas marking: batas circuit
        yang ditulis eksplisit oleh perancang P&ID. Belah ketupat = menempel pada pipa
        (jadi batas warna); lingkaran putus-putus = terbaca tapi tak menempel ke pipa
        mana pun (hanya informasi, dicek user)."""
        cps = (self.result or {}).get("conn_points", [])
        for c in cps:
            x, y, r = float(c["x"]), float(c["y"]), 13.0
            on_pipe = c.get("run_idx", -1) >= 0
            if on_pipe:
                poly = QtGui.QPolygonF([QtCore.QPointF(x, y - r), QtCore.QPointF(x + r, y),
                                        QtCore.QPointF(x, y + r), QtCore.QPointF(x - r, y)])
                it = QtWidgets.QGraphicsPolygonItem(poly)
                pen = QtGui.QPen(QtGui.QColor(20, 20, 25), 3)
            else:
                it = QtWidgets.QGraphicsEllipseItem(x - r, y - r, 2 * r, 2 * r)
                pen = QtGui.QPen(QtGui.QColor(120, 125, 135), 2, QtCore.Qt.DashLine)
            it.setPen(pen); it.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 235)))
            it.setZValue(50)
            it.setToolTip(f"Spec break: {c['codes'][0]} | {c['codes'][1]}"
                          + ("" if on_pipe else "  (not attached to any pipe — check)"))
            scene.addItem(it)
        return len(cps)

    def _fill_system_legend(self, systems, selected):
        for s in systems:
            pix = QtGui.QPixmap(16, 16); pix.fill(QtGui.QColor(*s["color"]))
            it = QtWidgets.QListWidgetItem(QtGui.QIcon(pix), f"  {s['fluid']}    ·    {s['n_pipes']} pipes")
            it.setData(QtCore.Qt.UserRole, s["fluid"])
            self.sys_list.addItem(it)
            if selected == s["fluid"]:
                it.setSelected(True)

    def _fill_system_members(self, systems, selected):
        """Isi tabel anggota utk system terpilih (kosong bila tampilan semua)."""
        t = self.sys_members; t.setRowCount(0)
        if selected is None:
            return
        pids = self.result["piping_ids"]
        for s in systems:
            if s["fluid"] != selected:
                continue
            for r, pi in enumerate(s["pid_idxs"]):
                t.insertRow(r)
                t.setItem(r, 0, QtWidgets.QTableWidgetItem(pids[pi].get("pid", "")))
                t.setItem(r, 1, QtWidgets.QTableWidgetItem(pids[pi].get("pclass", "")))

    def on_system_select(self, item):
        fl = item.data(QtCore.Qt.UserRole)
        self.render_system(selected=fl); self.statusBar().showMessage(f"isolating corrosion system: {fl}")

    def on_system_canvas_click(self, pt):
        if self.result is None or not getattr(self, "systems", None):
            return
        runs = self.result["runs"]; best_d, best_fl = 1e9, None
        for s in self.systems:
            for ri in s["run_idxs"]:
                if 0 <= ri < len(runs):
                    d = _seg_dist(pt.x(), pt.y(), runs[ri])
                    if d < best_d:
                        best_d, best_fl = d, s["fluid"]
        if best_fl and best_d <= 30:
            self.render_system(selected=best_fl); self.statusBar().showMessage(f"isolating: {best_fl}")

    # ---------- detection page ----------
    def _detection_page(self):
        page = QtWidgets.QWidget(); self.det_page = page
        outer = QtWidgets.QVBoxLayout(page); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        head = QtWidgets.QFrame(); head.setObjectName("Header"); head.setFixedHeight(66)
        hl = QtWidgets.QHBoxLayout(head); hl.setContentsMargins(14, 10, 24, 10)
        hl.addWidget(self._burger()); hl.addSpacing(8)
        tcol = QtWidgets.QVBoxLayout(); tcol.setSpacing(3)
        tcol.addWidget(self._lbl("P&ID Digitization", "Title"))
        hl.addLayout(tcol); hl.addSpacing(12)
        self.file_lbl = self._lbl("no file yet", "FileChip"); hl.addWidget(self.file_lbl)
        hl.addStretch(1)
        hl.addWidget(self._fit_btn(lambda: self._fit_view(self.view, self.scene)))
        self.btn_validate = QtWidgets.QPushButton("\U0001F4CA Report"); self.btn_validate.setObjectName("Secondary")
        self.btn_validate.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_validate.setToolTip("Report in 3 parts:\n• Digitization — AI model performance (confusion matrix, precision, mAP), refreshed whenever the models are retrained from your corrections.\n• Grouping — corrosion-circuit agreement vs the manual ground truth (Excel).\n• Marking — coverage & consistency.")
        self.btn_validate.clicked.connect(self.open_validation)
        hl.addWidget(self.btn_validate)
        self.btn_reviewed = QtWidgets.QPushButton("✓ Reviewed"); self.btn_reviewed.setObjectName("Secondary")
        self.btn_reviewed.setCheckable(True); self.btn_reviewed.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_reviewed.setToolTip("Mark this drawing as fully reviewed (stored together with the corrections)")
        self.btn_reviewed.clicked.connect(self._toggle_reviewed)
        hl.addWidget(self.btn_reviewed); hl.addSpacing(10)
        self.save_lbl = self._lbl("", "SaveClean"); hl.addWidget(self.save_lbl); hl.addSpacing(10)
        self.btn_open = QtWidgets.QPushButton("\U0001F4C2  Open P&ID"); self.btn_open.setObjectName("Secondary")
        self.btn_rotate = QtWidgets.QPushButton("⟳  Rotate"); self.btn_rotate.setObjectName("Secondary")
        self.btn_rotate.setToolTip(
            "Rotate the drawing 90° clockwise (Ctrl+R).\n\n"
            "Use this when a P&ID opens sideways — some drawings are drawn across a portrait\n"
            "sheet without setting the page rotation flag, so they render on their side.\n"
            "Symbol and piping-ID detection are unaffected by orientation, but line tracing is,\n"
            "so straighten the drawing before pressing Detect.\n\n"
            "The chosen rotation is remembered for this drawing.")
        self.btn_detect = QtWidgets.QPushButton("▶  Detect"); self.btn_detect.setObjectName("Primary")
        self.btn_save = QtWidgets.QPushButton("\U0001F4BE  Save"); self.btn_save.setObjectName("Secondary")
        self.btn_save.setToolTip("Ctrl+S — save corrections (reloaded automatically next time you open this file)")
        for b in (self.btn_open, self.btn_rotate, self.btn_detect, self.btn_save):
            b.setCursor(QtCore.Qt.PointingHandCursor); hl.addWidget(b)
        self.btn_open.clicked.connect(self.open_file); self.btn_detect.clicked.connect(self.start_detect)
        self.btn_rotate.clicked.connect(self.rotate_image)
        self.btn_save.clicked.connect(self.save)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+R"), self, activated=self.rotate_image)
        outer.addWidget(head)

        body = QtWidgets.QHBoxLayout(); body.setContentsMargins(18, 18, 18, 18); body.setSpacing(16)
        canvas_card = QtWidgets.QFrame(); canvas_card.setObjectName("Card")
        ccl = QtWidgets.QVBoxLayout(canvas_card); ccl.setContentsMargins(8, 8, 8, 8)
        self.scene = QtWidgets.QGraphicsScene(); self.view = View(self.scene)
        self.view.clicked.connect(self.on_canvas_click); self.view.doubleClicked.connect(self.on_canvas_dblclick)
        self.view.rightClicked.connect(self.on_canvas_rclick)
        self.view.banded.connect(self.on_canvas_band)
        ccl.addWidget(self.view)
        body.addWidget(canvas_card, 1); body.addWidget(self._right_panel())
        outer.addLayout(body, 1); return page

    def _divider(self):
        d = QtWidgets.QFrame(); d.setObjectName("Divider"); d.setFixedHeight(1)
        return d

    def _tool_btn(self, text, fn, tip):
        b = QtWidgets.QPushButton(text); b.setObjectName("Tool"); b.setToolTip(tip)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, btn=b, f=fn: self._run_tool(btn, f))
        return b

    # ---------- status alat aktif ----------
    def _paint_tool(self, btn, on):
        btn.setProperty("active", "true" if on else "false")
        btn.style().unpolish(btn); btn.style().polish(btn)

    def _clear_active_tool(self):
        b = getattr(self, "_active_tool", None)
        if b is not None and not sip.isdeleted(b):
            self._paint_tool(b, False)
        self._active_tool = None

    def _run_tool(self, btn, fn):
        """Sebuah alat yang sedang aktif dimatikan dengan menekan tombolnya lagi, karena
        tanpa itu satu-satunya jalan keluar adalah Escape — tidak terlihat di layar. Alat
        yang meninggalkan mode aktif (menunggu klik di gambar) ditandai supaya jelas mana
        yang sedang menunggu."""
        if getattr(self, "_active_tool", None) is btn:
            self._end_mode(); self.refresh()
            return self.statusBar().showMessage("tool turned off.")
        self._clear_active_tool()
        fn()
        if self.pick_mode:                       # alat ini menunggu klik -> tandai aktif
            self._active_tool = btn
            self._paint_tool(btn, True)

    def _right_panel(self):
        """Panel kanan digitisasi. Prinsip layout: TABEL piping ID = objek kerja utama ->
        satu-satunya yang melar (stretch); fitur eksploratif (filter simbol) pindah ke
        POPUP; tombol koreksi dipadatkan jadi context-bar 2 baris yang enable mengikuti
        seleksi. Semua penjelasan panjang hidup di tooltip, bukan memakan tinggi panel."""
        card = QtWidgets.QFrame(); card.setObjectName("Card"); card.setFixedWidth(378)
        v = QtWidgets.QVBoxLayout(card); v.setContentsMargins(16, 14, 16, 12); v.setSpacing(8)

        # ---- baris 1: layer tampilan (label inline, hemat 1 baris) ----
        row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
        lab = self._lbl("LAYER", "Mini"); lab.setFixedWidth(46); row.addWidget(lab)
        self.chk = {}
        for nm in ("Symbols", "Pipes", "Piping ID"):
            c = QtWidgets.QPushButton(nm); c.setObjectName("Chip")
            c.setCheckable(True); c.setChecked(True); c.setCursor(QtCore.Qt.PointingHandCursor)
            c.toggled.connect(self.update_visibility)
            row.addWidget(c); self.chk[nm] = c
        row.addStretch(1); v.addLayout(row)

        # ---- baris 2: filter simbol (popup) + review ----
        rowa = QtWidgets.QHBoxLayout(); rowa.setSpacing(8)
        self.btn_filter = QtWidgets.QPushButton("\U0001F3F7  Symbol Filter")
        self.btn_filter.setObjectName("Secondary")
        self.btn_filter.setToolTip("Highlight a specific symbol class on the drawing (PSV, Gate Valve, All Valves, ...) with its count.")
        self.btn_filter.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_filter.clicked.connect(self._open_filter_popup)
        self.btn_review = QtWidgets.QPushButton("⚠ Review"); self.btn_review.setObjectName("Review")
        self.btn_review.setToolTip("List of computed results worth double-checking: piping IDs with no pipe, lines with no piping ID, fluid codes suspected of being an OCR misread")
        self.btn_review.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_review.clicked.connect(self.open_review)
        rowa.addWidget(self.btn_filter, 1); rowa.addWidget(self.btn_review, 1)
        v.addLayout(rowa)
        self._build_filter_popup()
        v.addSpacing(2); v.addWidget(self._divider())

        # ---- daftar piping id: header sebaris (judul + [Semua] + [🔍 toggle] pojok kanan) ----
        self._col_filter = {"fluid": None, "pclass": None}   # filter per kolom (ala Excel)
        rowh = QtWidgets.QHBoxLayout(); rowh.setSpacing(6)
        rowh.addWidget(self._lbl("PIPING ID LIST", "Mini")); rowh.addStretch(1)
        self.btn_showall = QtWidgets.QPushButton("⟲ All"); self.btn_showall.setObjectName("Chip")
        self.btn_showall.setToolTip("Leave focus / isolation mode — show every piping ID again")
        self.btn_showall.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_showall.clicked.connect(self.show_all)
        rowh.addWidget(self.btn_showall)
        self.btn_search = QtWidgets.QPushButton(); self.btn_search.setObjectName("Chip")
        self.btn_search.setIcon(self._search_icon()); self.btn_search.setIconSize(QtCore.QSize(16, 16))
        self.btn_search.setCheckable(True); self.btn_search.setFixedWidth(34)
        self.btn_search.setToolTip("Search (like Ctrl+F): show / hide the search box.")
        self.btn_search.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_search.toggled.connect(self._toggle_search)
        rowh.addWidget(self.btn_search)
        v.addLayout(rowh)

        # ---- search bar: TERSEMBUNYI, muncul saat 🔍 diklik (seperti Ctrl+F di PDF) ----
        self.search_bar = QtWidgets.QWidget()
        sh = QtWidgets.QHBoxLayout(self.search_bar); sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(6)
        self.pid_search = QtWidgets.QLineEdit()
        self.pid_search.setPlaceholderText("search piping ID / fluid / class…")
        self.pid_search.setClearButtonEnabled(True)
        self.pid_search.textChanged.connect(self._apply_pid_filter)
        sh.addWidget(self.pid_search, 1)
        x = QtWidgets.QPushButton("✕"); x.setObjectName("Chip"); x.setFixedWidth(28)
        x.setCursor(QtCore.Qt.PointingHandCursor); x.setToolTip("Close search")
        x.clicked.connect(lambda: self.btn_search.setChecked(False))
        sh.addWidget(x)
        self.search_bar.setVisible(False)
        v.addWidget(self.search_bar)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Piping ID", "Fluid ▾", "Class ▾", "Pipe?"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 168); self.table.setColumnWidth(1, 56); self.table.setColumnWidth(2, 56)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setToolTip("Click a row → its pipe is highlighted on the drawing.\nClick a pipe on the drawing → its row is selected here.\nClick the 'Fluid ▾' / 'Class ▾' header → filter (Excel style).")
        hdr = self.table.horizontalHeader()
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header_click)
        self.table.itemSelectionChanged.connect(self.on_select)
        v.addWidget(self.table, 1)                          # <- SATU-SATUNYA yang melar
        self.lbl_count = self._lbl("—", "Subtle"); v.addWidget(self.lbl_count)
        v.addSpacing(2); v.addWidget(self._divider())

        # ---- context-bar koreksi: 2 baris kompak, label inline, enable saat ada seleksi ----
        v.addWidget(self._lbl("CORRECTIONS  —  select a piping ID / pipe first", "Mini"))
        self._sel_tools = []                                # tombol yang butuh seleksi

        def _short(text, fn, tip, need_sel=True):
            b = self._tool_btn(text, fn, tip)
            if need_sel:
                self._sel_tools.append(b)
            return b

        r1 = QtWidgets.QHBoxLayout(); r1.setSpacing(6)
        lab1 = self._lbl("PIPE", "Mini"); lab1.setFixedWidth(34); r1.addWidget(lab1)
        r1.addWidget(_short("\U0001F5B1 Reassign", self.pick_pipe,
                     "The highlighted pipe is WRONG but the right one is already traced?\nClick this button, then click the correct pipe on the drawing."), 1)
        r1.addWidget(_short("✏ Trace", self.trace_manual,
                     "The correct pipe was NOT detected?\nClick this button, click points along the pipe, then DOUBLE-CLICK to finish."), 1)
        r1.addWidget(_short("\U0001F5D1 Delete", self.delete_pipe,
                     "Delete a wrong pipe LINE (it is also detached from its piping ID).\nYou can also right-click any line on the drawing."), 1)
        v.addLayout(r1)

        r2 = QtWidgets.QHBoxLayout(); r2.setSpacing(6)
        lab2 = self._lbl("ID", "Mini"); lab2.setFixedWidth(34); r2.addWidget(lab2)
        r2.addWidget(_short("\U0001F3F7 Edit", self.edit_pid,
                     "Fix a misread piping ID / fluid text."), 1)
        r2.addWidget(_short("⬚ Box", self.move_pid_box,
                     "Redraw the position box of the selected piping ID:\nclick-HOLD, then drag a box over the text on the drawing."), 1)
        r2.addWidget(_short("＋ Add", self.add_pid,
                     "Add a piping ID that was not detected: type its text,\nthen click-HOLD & drag a box over the text on the drawing.", need_sel=False), 1)
        r2.addWidget(_short("✕ Delete", self.delete_pid, "Delete the selected piping ID."), 1)
        v.addLayout(r2)

        r3 = QtWidgets.QHBoxLayout(); r3.setSpacing(6)
        lab3 = self._lbl("+", "Mini"); lab3.setFixedWidth(34); r3.addWidget(lab3)
        r3.addWidget(_short("\U0001F50D Box OCR", self.ocr_pid_box,
                     "Piping ID not detected? Click this button, then DRAW A BOX around its text on\nthe drawing — the system OCRs just that patch and fills it in (you can still correct it).",
                     need_sel=False), 1)
        r3.addWidget(_short("\U0001F393 Teach parsing", self.teach_parsing,
                     "Company format not recognised? Pick one piping ID and mark which token is the\nprocess fluid and which is the piping class — the system applies it to EVERY piping ID."), 1)
        v.addLayout(r3)

        r4 = QtWidgets.QHBoxLayout(); r4.setSpacing(6)
        lab4 = self._lbl("BOXES", "Mini"); lab4.setFixedWidth(34); r4.addWidget(lab4)
        self.btn_boxedit = self._tool_btn(
            "✎ Edit Boxes", self.toggle_box_edit,
            "Fix ANY detection box (symbol / equipment / piping ID):\nturn this on, click a box → drag a corner = resize, drag the centre = move.\nRight-click a box = delete.")
        self.btn_boxedit.setCheckable(True)
        r4.addWidget(self.btn_boxedit, 1)
        r4.addWidget(_short("＋ Symbol", self.add_symbol_box,
                     "Object not detected? Add a new box: pick the class (Equipment / Valve / Instrument), then drag a rubber-band box on the drawing.\nEvery correction and addition becomes TRAINING DATA when you save.",
                     need_sel=False), 1)
        v.addLayout(r4)

        r5 = QtWidgets.QHBoxLayout(); r5.setSpacing(6)
        lab5 = self._lbl("TRAIN", "Mini"); lab5.setFixedWidth(34); r5.addWidget(lab5)
        self.btn_feedback = _short("\U0001F393 Training data", self.show_feedback_info,
                     "How many corrected drawings are already training data, and how to\nretrain the detector so it keeps improving.", need_sel=False)
        r5.addWidget(self.btn_feedback, 1)
        v.addLayout(r5)
        self._update_tool_state()

        foot = self._lbl("Undo: Ctrl+Z  ·  Esc: cancel  ·  Pan: scroll  ·  Zoom: Ctrl+scroll", "Subtle")
        v.addWidget(foot)
        return card

    # ---------- popup filter simbol ----------
    def _build_filter_popup(self):
        """Popup filter simbol (chip induk + chip subtype). Qt.Popup -> nutup sendiri saat
        klik di luar. Chip di-rebuild oleh _rebuild_symbol_chips (dipanggil _fill_table &
        saat popup dibuka) — layout hidup di sini, tak makan tinggi panel kanan."""
        self.filter_pop = QtWidgets.QWidget(self, QtCore.Qt.Popup)
        self.filter_pop.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        out = QtWidgets.QVBoxLayout(self.filter_pop); out.setContentsMargins(0, 0, 0, 0)
        inner = QtWidgets.QFrame(); inner.setObjectName("Card"); out.addWidget(inner)
        iv = QtWidgets.QVBoxLayout(inner); iv.setContentsMargins(14, 12, 14, 12); iv.setSpacing(8)

        head = QtWidgets.QHBoxLayout(); head.setSpacing(8)
        head.addWidget(self._lbl("SYMBOL FILTER  —  click a class to highlight it on the drawing", "Mini"))
        head.addStretch(1)
        rst = QtWidgets.QPushButton("Reset"); rst.setObjectName("Chip")
        rst.setToolTip("Clear every filter — show all symbols again")
        rst.setCursor(QtCore.Qt.PointingHandCursor); rst.clicked.connect(self._reset_sym_filter)
        head.addWidget(rst)
        iv.addLayout(head)

        self.sym_parent_row = QtWidgets.QHBoxLayout(); self.sym_parent_row.setSpacing(6)
        iv.addLayout(self.sym_parent_row)                   # chip induk: Semua Valve/Instr/Equip
        sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame); sc.setFixedHeight(300)
        host = QtWidgets.QWidget(); self.sym_chip_grid = QtWidgets.QGridLayout(host)
        self.sym_chip_grid.setContentsMargins(0, 0, 0, 0); self.sym_chip_grid.setSpacing(6)
        self.sym_chip_grid.setAlignment(QtCore.Qt.AlignTop)
        sc.setWidget(host); iv.addWidget(sc)
        self.sym_chips = {}; self.sym_parent_chips = {}
        self.filter_pop.setFixedWidth(360)

    def _open_filter_popup(self):
        if self.result is None:
            return self.statusBar().showMessage("no result yet — open a P&ID and run Detect first.")
        self._rebuild_symbol_chips()
        self.filter_pop.adjustSize()
        # sejajarkan tepi KANAN popup dgn tepi kanan tombol; jaga tetap di layar
        br = self.btn_filter.mapToGlobal(QtCore.QPoint(self.btn_filter.width(),
                                                       self.btn_filter.height() + 6))
        x = max(8, br.x() - self.filter_pop.width())
        self.filter_pop.move(x, br.y()); self.filter_pop.show()

    def _reset_sym_filter(self):
        self.sym_filter.clear(); self.sym_filter_coarse.clear()
        self._rebuild_symbol_chips(); self.refresh(); self._sym_status()

    def _update_filter_badge(self):
        n = len(self.sym_filter) + len(self.sym_filter_coarse)
        self.btn_filter.setText("\U0001F3F7  Symbol Filter" + (f"  ·  {n} active" if n else ""))

    def _update_tool_state(self):
        """Tombol koreksi yang butuh seleksi: enable hanya saat ada baris terpilih."""
        on = getattr(self, "sel_row", -1) >= 0
        for b in getattr(self, "_sel_tools", []):
            b.setEnabled(on)

    def _img_label(self, path, maxw=560):
        """QLabel berisi gambar metrik (confusion matrix/PR) diskalakan, non-ASCII safe."""
        lab = QtWidgets.QLabel()
        pix = QtGui.QPixmap(path)
        if pix.isNull():
            lab.setText("(image unreadable)")
            return lab
        if pix.width() > maxw:
            pix = pix.scaledToWidth(maxw, QtCore.Qt.SmoothTransformation)
        lab.setPixmap(pix)
        return lab

    def _tab_digitization(self):
        """Tab performa MODEL AI: kartu metrik + confusion matrix + PR curve. Update saat retrain."""
        from pidcorr import report_metrics as RM
        import time
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        v.addWidget(QtWidgets.QLabel(
            "<b>Perception-layer (AI) performance</b> on labelled data. The numbers and charts are regenerated every time a model is retrained — the more you correct the detections (add equipment / remove wrong boxes) and retrain, the better and more current they get."))
        for m in RM.model_metrics():
            box = QtWidgets.QFrame(); box.setObjectName("Card")
            bl = QtWidgets.QVBoxLayout(box)
            if not m["exists"]:
                bl.addWidget(QtWidgets.QLabel(f"<b>{m['name']}</b> — not trained yet."))
                v.addWidget(box); continue
            upd = (time.strftime('%d %b %Y', time.localtime(m["updated"]))
                   if m.get("updated") else "-")
            if m["kind"] == "classify":
                line = f"akurasi {m.get('acc',0)*100:.1f}%"
            else:
                line = (f"mAP@50 <b>{m.get('map50',0)*100:.1f}%</b> · "
                        f"mAP@50-95 {m.get('map',0)*100:.1f}% · "
                        f"precision {m.get('precision',0)*100:.1f}% · "
                        f"recall {m.get('recall',0)*100:.1f}%")
            bl.addWidget(QtWidgets.QLabel(
                f"<b>{m['name']}</b><br>{line}<br>"
                f"<span style='color:#64748B'>{m['epochs']} epochs · updated {upd}</span>"))
            imgs = QtWidgets.QHBoxLayout()
            if m.get("confusion"):
                col = QtWidgets.QVBoxLayout(); col.addWidget(QtWidgets.QLabel("Confusion matrix"))
                col.addWidget(self._img_label(m["confusion"], 360)); imgs.addLayout(col)
            if m.get("pr_curve"):
                col = QtWidgets.QVBoxLayout(); col.addWidget(QtWidgets.QLabel("Precision-Recall"))
                col.addWidget(self._img_label(m["pr_curve"], 360)); imgs.addLayout(col)
            imgs.addStretch(1); bl.addLayout(imgs)
            v.addWidget(box)
        v.addWidget(QtWidgets.QLabel(
            "<span style='color:#64748B'>Retrain the equipment detector from your corrections: <code>python scripts/train_equip_big.py</code> (data/feedback is included automatically), then reopen the Report.</span>"))
        v.addStretch(1)
        sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(w)
        return sc

    def _tab_grouping(self):
        """Tab pengelompokan: kedekatan corrosion CIRCUIT vs ground truth Excel + cek konsistensi."""
        from pidcorr.validate import validate_grouping, format_report
        from pidcorr import groundtruth as G
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        # perbandingan vs ground truth manual (Excel) utk drawing ini
        gt_box = QtWidgets.QFrame(); gt_box.setObjectName("Card"); gv = QtWidgets.QVBoxLayout(gt_box)
        gv.addWidget(QtWidgets.QLabel("<b>Corrosion-circuit agreement vs the manual ground truth (Excel)</b>"))
        try:
            stem = os.path.splitext(os.path.basename(self.image_path))[0]
            g = G.compare_by_stem(self.result, stem)
        except Exception as e:
            g = {"in_gt": False, "err": str(e)}
        if not g.get("in_gt"):
            gv.addWidget(QtWidgets.QLabel(
                "This drawing is not in the Excel ground truth — the corpus-wide comparison can be run with <code>python scripts/eval_grouping_vs_gt.py</code>."))
        elif g.get("n_matched", 0) < 2:
            gv.addWidget(QtWidgets.QLabel("Fewer than 2 piping IDs match the Excel sheet — not enough to compare."))
        else:
            gv.addWidget(QtWidgets.QLabel(
                f"Partition agreement (color-agnostic, level circuit): <b>{g['pct']:.1f}%</b> "
                f"of {g['n_pair']} pairs · {g['n_matched']}/{g['n_gt']} piping IDs matched.<br>"
                f"<span style='color:#64748B'>engineer-merged / system-split {g['eng_merge']} · "
                f"system-merged / engineer-split {g['sys_merge']} pairs (usually the engineer's "
                f"damage-mechanism judgment, outside the systemization scope).</span>"))
        v.addWidget(gt_box)
        # cek konsistensi runtime (invariant)
        rep = validate_grouping(self.result)
        txt = QtWidgets.QPlainTextEdit(format_report(rep)); txt.setReadOnly(True)
        txt.setFont(QtGui.QFont("Consolas", 9)); v.addWidget(txt, 1)
        return w

    def _tab_marking(self):
        from pidcorr.validate import validate_marking, format_report
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        # --- perbandingan vs CCD marked (color-agnostic): placement + boundary ---
        gt_box = QtWidgets.QFrame(); gt_box.setObjectName("Card"); gv = QtWidgets.QVBoxLayout(gt_box)
        gv.addWidget(QtWidgets.QLabel("<b>Marking vs CCD marked engineer (color-agnostic)</b>"))
        try:
            from pidcorr import marking_eval as ME
            stem = os.path.splitext(os.path.basename(self.image_path))[0]
            m = ME.evaluate_by_stem(self.result, stem)
        except Exception as e:
            m = {"err": str(e)}
        if m is None:
            gv.addWidget(QtWidgets.QLabel(
                "No marked CCD PDF found for this drawing — run the corpus aggregate with <code>python scripts/eval_marking_vs_ccd.py</code>."))
        elif m.get("skip"):
            gv.addWidget(QtWidgets.QLabel("Raster drawing — ground-truth colour extraction does not apply."))
        elif m.get("err"):
            gv.addWidget(QtWidgets.QLabel(f"(failed: {m['err']})"))
        else:
            a, b = m["placement"], m["boundary"]
            gv.addWidget(QtWidgets.QLabel(
                f"<b>A. Tracing placement accuracy</b> — does the marking land on the pipes the "
                f"engineer marked:<br>&nbsp;&nbsp;F1 <b>{a['f1']:.0f}%</b> "
                f"(precision {a['precision']:.0f}% / recall {a['recall']:.0f}%) · "
                f"{a['tp']} pipes matched out of {a['n_gt_marked']} (engineer) / {a['n_sys_marked']} (system)."))
            ag = b['agreement']
            gv.addWidget(QtWidgets.QLabel(
                f"<b>B. Colour-change location accuracy</b> — is the circuit boundary at the "
                f"same point:<br>&nbsp;&nbsp;agreement {ag:.0f}% of {b['n_edges']} junctions "
                f"(boundary precision {b['precision']:.0f}% / recall {b['recall']:.0f}%).<br>"
                f"<span style='color:#64748B'>Colour itself is NOT compared — only where the marking "
                f"is and where it changes. Boundary differences usually reflect damage-mechanism "
                f"judgment by the engineer (outside this scope).</span>"))
        v.addWidget(gt_box)
        # --- konsistensi internal + cakupan ---
        v.addWidget(QtWidgets.QLabel(
            "<b>Internal consistency</b> — colours match the groups, every pipe is coloured, pipe-length coverage."))
        rep = validate_marking(self.result)
        txt = QtWidgets.QPlainTextEdit(format_report(rep)); txt.setReadOnly(True)
        txt.setFont(QtGui.QFont("Consolas", 9)); v.addWidget(txt, 1)
        return w

    def open_validation(self):
        """Dialog REPORT 3 bagian: (1) performa model AI digitisasi (confusion matrix, mAP —
        update saat retrain), (2) kedekatan corrosion circuit vs ground truth manual (Excel),
        (3) marking. Sistem yang mengukur; manusia menindaklanjuti."""
        if self.result is None:
            return self.statusBar().showMessage("no result yet — open a P&ID and run Detect first.")
        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("Report")
        dlg.resize(860, 720)
        lay = QtWidgets.QVBoxLayout(dlg)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._tab_digitization(), "Digitization (AI models)")
        tabs.addTab(self._tab_grouping(), "Grouping")
        tabs.addTab(self._tab_marking(), "Marking")
        lay.addWidget(tabs, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject); bb.accepted.connect(dlg.accept)
        bb.button(QtWidgets.QDialogButtonBox.Close).setText("Close")
        lay.addWidget(bb)
        self.statusBar().showMessage("Report opened — digitization (AI models), grouping (vs ground truth), marking.")
        dlg.exec_()

    def _dedup_loaded(self) -> bool:
        """Rapikan hasil TERSIMPAN LAMA: (1) buang piping ID di title block/notes/tabel
        (referensi dokumen, bukan label pipa), (2) gabungkan duplikat nomor-line jadi 1 entri
        register + union run. Idempoten, aman pada hasil baru; pid manual TIDAK dibuang.
        Return True bila ada perubahan (supaya bisa ditandai perlu-simpan)."""
        if not (self.result and self.result.get("piping_ids")):
            return False
        pids = self.result["piping_ids"]
        before = len(pids)
        furn = self.result.get("furniture") or []
        if furn:
            def _in_furn(p):
                cx = (p["x1"] + p["x2"]) / 2; cy = (p["y1"] + p["y2"]) / 2
                return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in furn)
            pids = [p for p in pids if p.get("manual") or not _in_furn(p)]
        for r in pids:
            r.setdefault("extra_runs", [])
        pids = pipeline._dedup_pid_recs(pids)
        self.result["piping_ids"] = pids
        return len(pids) < before

    # ---------- file / detect ----------
    def open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open P&ID", "", "P&ID (*.pdf *.png *.jpg *.jpeg)")
        if not path:
            return
        self.image_path = path; self.file_lbl.setText(os.path.basename(path))
        self.statusBar().showMessage("rendering image…"); QtWidgets.QApplication.processEvents()
        base = pipeline.load_image(path, dpi=350)
        saved = self._saved_rot()
        auto = False
        if saved is None and base is not None and base.shape[0] > base.shape[1]:
            # P&ID praktis selalu dicetak landscape. Halaman potret berarti gambarnya
            # tersimpan menyamping — dibetulkan di muka, karena line tracing memburuk
            # kalau sheet-nya miring. Sekali klik Rotate mengembalikannya.
            self.rot, auto = 270, True
        else:
            self.rot = saved or 0
        self.img_bgr = pipeline.rotate_bgr(base, self.rot)
        if auto:
            self._write_rot()
        self.result = None; self.sel_row = -1; self.undo_stack = []; self._show_base()
        self._update_rot_label()
        rp = self._result_path()
        if os.path.exists(rp):
            try:
                self.result = pipeline.load_result(rp)
                if int(self.result.get("rot", 0)) % 360 != self.rot:
                    # koordinat tersimpan memakai orientasi lain -> memakainya akan
                    # menempatkan setiap kotak di posisi yang salah
                    self.result = None
                    self._set_dirty(False); self.btn_reviewed.setChecked(False)
                    self.statusBar().showMessage(
                        "this drawing was stored sideways and has been turned upright — "
                        "the saved corrections belong to the old orientation, so click "
                        "'Detect' again (or press Rotate to go back)."
                        if auto else
                        "the saved corrections were made at a different rotation — "
                        "click 'Detect' again.")
                    return
                merged = self._dedup_loaded()            # rapikan duplikat nomor-line hasil lama
                self.refresh(); self._set_dirty(merged)  # bila ada yg digabung -> perlu Simpan
                self.btn_reviewed.setChecked(bool(self.result.get("reviewed")))
                self.statusBar().showMessage(
                    "duplicate piping IDs cleaned up — press Save (Ctrl+S) to keep it."
                    if merged else "saved corrections loaded automatically.")
            except Exception:
                # file koreksi korup (mis. penyimpanan lama terputus) -> amankan, JANGAN crash
                bak = rp + ".corrupt.bak"
                try:
                    os.replace(rp, bak)
                except OSError:
                    pass
                self.result = None; self._set_dirty(False); self.btn_reviewed.setChecked(False)
                self.statusBar().showMessage("the old correction file is corrupt — it has been set aside; click 'Detect' again.")
                QtWidgets.QMessageBox.warning(
                    self, "Corrupt correction file",
                    "The saved correction file for this drawing is corrupt (a previous save was probably interrupted), so it was ignored and set aside as:\n"
                    f"{os.path.basename(bak)}\n\nPlease click 'Detect' again, then Save.")
        else:
            self._set_dirty(False); self.btn_reviewed.setChecked(False)
            self.statusBar().showMessage(
                "this drawing was stored sideways and has been turned upright — "
                "click 'Detect'." if auto else "image ready — click 'Detect'.")

    def _show_base(self):
        # pertahankan zoom/pan lintas refresh (fit hanya saat gambar BARU) -> edit kotak
        # yang butuh zoom-in tidak ter-reset tiap seleksi.
        keep = self.img_bgr is not None and getattr(self, "_base_token", None) == id(self.img_bgr)
        tr = self.view.transform() if keep else None
        center = self.view.mapToScene(self.view.viewport().rect().center()) if keep else None
        self.scene.clear(); self.layers = {}; self.handles = []; self._edit_items = []
        if self.img_bgr is not None:
            self.scene.addPixmap(bgr_to_qpix(self.img_bgr))
            self.scene.setSceneRect(QtCore.QRectF(self.scene.itemsBoundingRect()))
            if keep:
                self.view.setTransform(tr); self.view.centerOn(center)
            else:
                self.view.fitInView(self.scene.sceneRect(), QtCore.Qt.KeepAspectRatio)
            self._base_token = id(self.img_bgr)

    # ---------- rotation ----------
    def _rot_path(self):
        return self._cache_base() + ".rot.json"

    def _saved_rot(self):
        """Rotasi yang pernah dipilih untuk gambar ini, atau None kalau belum pernah."""
        try:
            with open(self._rot_path(), encoding="utf-8") as f:
                return int(json.load(f).get("rot", 0)) % 360
        except Exception:
            return None

    def _load_rot(self):
        r = self._saved_rot()
        return 0 if r is None else r

    def _write_rot(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        try:
            with open(self._rot_path(), "w", encoding="utf-8") as f:
                json.dump({"rot": self.rot}, f)
        except OSError:
            pass

    def _update_rot_label(self):
        self.btn_rotate.setText("⟳  Rotate" if not self.rot else f"⟳  Rotate ({self.rot}°)")

    def rotate_image(self):
        """Putar 90° searah jarum jam. Hasil deteksi lama dibuang, bukan diputar ikut:
        memutar sebagian koordinat dan melewatkan satu kunci akan merusak hasil tanpa
        terlihat, sedangkan deteksi ulang selalu benar."""
        if self.img_bgr is None:
            return self.statusBar().showMessage("open a drawing first.")
        if self.result is not None:
            r = QtWidgets.QMessageBox.question(
                self, "Rotate drawing",
                "Rotating discards the current detection, because its coordinates belong to "
                "the present orientation.\n\nAny unsaved corrections will be lost. "
                "Rotate and detect again?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if r != QtWidgets.QMessageBox.Yes:
                return
        self.rot = (self.rot + 90) % 360
        self.img_bgr = pipeline.rotate_bgr(self.img_bgr, 90)
        self.result = None; self.sel_row = -1; self.undo_stack = []
        self._write_rot()
        self._update_rot_label(); self._show_base(); self.refresh()
        self._set_dirty(False); self.btn_reviewed.setChecked(False)
        self.statusBar().showMessage(
            f"rotated to {self.rot}° — click 'Detect' to digitize at this orientation.")

    def _cache_base(self):
        return os.path.join(CACHE_DIR, os.path.splitext(os.path.basename(self.image_path))[0])

    def _result_path(self):
        return self._cache_base() + ".pidcorr.json"

    def start_detect(self):
        if self.img_bgr is None:
            return self.statusBar().showMessage("open a drawing first.")
        # deteksi membangun daftar simbol dari nol -> hasil lama disimpan dulu supaya kotak
        # yang sudah dikoreksi/ditambah tangan bisa dikembalikan (lihat on_detected)
        self._pre_detect = self.result
        self.btn_detect.setEnabled(False); self.overlay.set_status("memulai…"); self.overlay.show_over()
        self.worker = DetectWorker(self.img_bgr, self.image_path, 350,
                                   self._cache_base() + ".pids.json", self.rot)
        self.worker.progress.connect(self._on_progress); self.worker.done.connect(self.on_detected)
        self.worker.failed.connect(self.on_failed); self.worker.start()

    def _on_progress(self, m): self.overlay.set_status(m); self.statusBar().showMessage(m)

    def on_detected(self, res):
        # Kotak hasil koreksi tangan dikembalikan ke hasil baru. Yang sudah ditemukan lagi
        # oleh detektor (tumpang tindih tinggi) TIDAK digandakan, jadi mendeteksi berulang
        # kali tidak menumpuk kotak di objek yang sama.
        kept = pipeline.merge_manual_symbols(res, getattr(self, "_pre_detect", None))
        self._pre_detect = None
        self.result = res; self.undo_stack = []; self.sel_row = -1
        self.btn_reviewed.setChecked(False)               # deteksi ulang = review ulang
        self.refresh()
        self.overlay.hide(); self.btn_detect.setEnabled(True); self._set_dirty(True)
        self.statusBar().showMessage(
            (f"detection finished — {kept} manually corrected box"
             f"{'es' if kept > 1 else ''} kept. Review, then Save (Ctrl+S)." if kept else
             "detection finished — make corrections, then Save (Ctrl+S)."))

    def on_failed(self, e):
        self.overlay.hide(); self.btn_detect.setEnabled(True)
        QtWidgets.QMessageBox.critical(self, "Failed", "Detection failed:\n" + e)

    # ---------- render ----------
    def _run_verts(self, run):
        pts = run.get("points")
        if pts and len(pts) >= 2:
            return [list(q) for q in pts]
        return [[run["x1"], run["y1"]], [run["x2"], run["y2"]]]

    def _set_run_verts(self, run, verts):
        run["points"] = [list(v) for v in verts]
        run["x1"], run["y1"] = verts[0]; run["x2"], run["y2"] = verts[-1]

    def _path(self, verts):
        p = QtGui.QPainterPath(QtCore.QPointF(*verts[0]))
        for v in verts[1:]:
            p.lineTo(QtCore.QPointF(*v))
        return p

    def _add_box(self, group, p, pen, brush=None):
        it = QtWidgets.QGraphicsRectItem(p["x1"], p["y1"], p["x2"] - p["x1"], p["y2"] - p["y1"])
        it.setPen(pen)
        if brush is not None:
            it.setBrush(brush)
        group.addToGroup(it)

    def _run_len(self, r):
        v = self._run_verts(r)
        return sum(((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5 for a, b in zip(v, v[1:]))

    def _table_run_idxs(self, runs):
        """Garis TABEL/title-block (layout-agnostic): (1) cari garis 'grid' = punya >=3
        tetangga SEJAJAR berdekatan+tumpang-tindih (baris/kolom sel), (2) gabung jadi
        REGION tabel (bbox), (3) buang SEMUA run di dalam region tabel yg cukup besar
        (termasuk border luar tabel). Pipa jarang punya banyak garis sejajar rapat."""
        S = self.result.get("dpi", 350) / 72.0
        near = 20 * S

        def bb(r):
            return [min(r["x1"], r["x2"]), min(r["y1"], r["y2"]),
                    max(r["x1"], r["x2"]), max(r["y1"], r["y2"])]

        grid = set()
        for axis in ("h", "v"):
            grp = [(i, r) for i, r in enumerate(runs) if r.get("axis") == axis]
            for i, r in grp:
                ci = r["y1"] if axis == "h" else r["x1"]
                b = bb(r); lo_i, hi_i = (b[0], b[2]) if axis == "h" else (b[1], b[3])
                cnt = 0
                for j, s in grp:
                    if j == i:
                        continue
                    cj = s["y1"] if axis == "h" else s["x1"]
                    if abs(ci - cj) <= near:
                        bs = bb(s); lo_j, hi_j = (bs[0], bs[2]) if axis == "h" else (bs[1], bs[3])
                        if min(hi_i, hi_j) - max(lo_i, lo_j) > 0.4 * min(hi_i - lo_i, hi_j - lo_j):
                            cnt += 1
                if cnt >= 3:
                    grid.add(i)

        # gabung bbox garis grid -> region tabel (beberapa pass merge)
        boxes = [bb(runs[i]) for i in grid]
        pad = 45 * S
        for _ in range(5):
            merged = []
            for b in boxes:
                hit = None
                for m in merged:
                    if b[0] <= m[2]+pad and b[2] >= m[0]-pad and b[1] <= m[3]+pad and b[3] >= m[1]-pad:
                        hit = m; break
                if hit:
                    hit[0] = min(hit[0], b[0]); hit[1] = min(hit[1], b[1])
                    hit[2] = max(hit[2], b[2]); hit[3] = max(hit[3], b[3])
                else:
                    merged.append(list(b))
            if len(merged) == len(boxes):
                break
            boxes = merged
        boxes = [m for m in boxes if (m[2]-m[0]) > 90*S and (m[3]-m[1]) > 45*S]   # region cukup besar

        bad = set(grid)
        for i, r in enumerate(runs):
            cx = (r["x1"]+r["x2"])/2; cy = (r["y1"]+r["y2"])/2
            for m in boxes:      # margin lebih lebar -> ikut buang border luar tabel
                if m[0]-18*S <= cx <= m[2]+18*S and m[1]-18*S <= cy <= m[3]+18*S:
                    bad.add(i); break
        return bad

    def _mk_move(self, run, i, path):
        """Closure: geser dot vertex-i milik run ini -> update data + garis live."""
        def move(pos):
            verts = self._run_verts(run); verts[i] = [pos.x(), pos.y()]
            self._set_run_verts(run, verts); path.setPath(self._path(verts)); self._set_dirty(True)
        return move

    def _draw_pipe(self, run, focus, dots=True):
        verts = self._run_verts(run)
        path = QtWidgets.QGraphicsPathItem(self._path(verts))
        path.setPen(QtGui.QPen(PIPE_FOCUS if focus else PIPE_BLUE, 6 if focus else 3))
        self.layers["Pipes"].addToGroup(path)
        if dots:                                             # DOT di tiap ujung -> bisa digeser
            for i, vtx in enumerate(verts):
                hd = Handle(vtx[0], vtx[1], self._snapshot, self._mk_move(run, i, path))
                self.scene.addItem(hd); self.handles.append(hd)

    def refresh(self):
        if self.result is None:
            return
        self._show_base()
        for nm in ("Symbols", "Pipes", "Piping ID"):
            g = QtWidgets.QGraphicsItemGroup(); self.scene.addItem(g); self.layers[nm] = g
        runs = self.result["runs"]; pids = self.result["piping_ids"]
        focus = 0 <= self.sel_row < len(pids)

        if focus:
            # ISOLASI: hanya pipa piping ID terpilih (biru tua) + dot
            p = pids[self.sel_row]; ri = p.get("run_idx", -1)
            if ri is not None and 0 <= ri < len(runs):
                self._draw_pipe(runs[ri], focus=True)
            self._add_box(self.layers["Piping ID"], p, QtGui.QPen(PID_FOCUS, 5),
                          QtGui.QBrush(PID_FOCUS_FILL))
        else:
            # SEMUA trace pipa (biru, 1 warna) + dot. Buang segmen noise pendek: tampilkan run
            # yg cukup panjang (main) ATAU yg sudah punya piping ID.
            main_px = 45 * self.result.get("dpi", 350) / 72.0
            pointed = {p.get("run_idx", -1) for p in pids if p.get("run_idx", -1) >= 0}
            tables = self._table_run_idxs(runs)
            for idx, r in enumerate(runs):
                if idx in tables and idx not in pointed:            # buang garis tabel/grid
                    continue
                if r.get("underline") and idx not in pointed:       # garis penunjuk ber-label != pipa
                    continue
                if idx in pointed or self._run_len(r) >= main_px:
                    self._draw_pipe(r, focus=False, dots=False)     # overview: garis saja, tanpa dot
            filt_on = bool(self.sym_filter or self.sym_filter_coarse)
            for i, s in enumerate(self.result.get("symbols", [])):
                if self.box_sel == ("symbol", i):
                    continue                                 # digambar overlay edit (jangan dobel)
                if not self._sym_visible(s):
                    continue                                 # filter aktif -> sorot yg dipilih saja
                c = COARSE_COL.get(s["coarse"], (128, 128, 128))
                self._add_box(self.layers["Symbols"], s,
                              QtGui.QPen(QtGui.QColor(c[2], c[1], c[0]), 4 if filt_on else 2))
                # nama equipment yg diberi user (mis. 'LUBE OIL COOLER') -> tulis di kotak
                # supaya JELAS sudah tertambah (dulu kotak tanpa teks -> terlihat tak masuk)
                sub = (s.get("subtype") or "").strip()
                if sub and s["coarse"] == "equipment":
                    t = QtWidgets.QGraphicsSimpleTextItem(sub)
                    t.setBrush(QtGui.QBrush(QtGui.QColor(c[2], c[1], c[0])))
                    fnt = t.font(); fnt.setPointSize(11); fnt.setBold(True); t.setFont(fnt)
                    t.setPos(s["x1"] + 3, s["y1"] - 20)
                    self.layers["Symbols"].addToGroup(t)
            for i, p in enumerate(pids):
                if self.box_sel == ("pid", i):
                    continue
                # GARIS ungu saja (tanpa isian) -> berwarna & jelas tapi TIDAK berat/berantakan
                self._add_box(self.layers["Piping ID"], p, QtGui.QPen(NEUTRAL_PID, 2))
        if self.box_edit and self.box_sel:                   # pasang ulang overlay editable
            self._begin_box_edit()
        self.update_visibility(); self._fill_table()

    def update_visibility(self):
        for nm, g in self.layers.items():
            g.setVisible(self.chk[nm].isChecked())
        for hd in self.handles:
            hd.setVisible(self.chk["Pipes"].isChecked())

    def _fill_table(self):
        pids = self.result["piping_ids"]; self.table.blockSignals(True); self.table.setRowCount(len(pids))
        for i, p in enumerate(pids):
            has = "✔" if p.get("run_idx", -1) >= 0 else "—"
            for c, val in enumerate([p["pid"], p["fluid"], p["pclass"], has]):
                self.table.setItem(i, c, QtWidgets.QTableWidgetItem(str(val)))
        self.table.blockSignals(False)
        self._apply_pid_filter()                    # terapkan filter/search yg sedang aktif
        n = sum(1 for p in pids if p.get("run_idx", -1) >= 0)
        self.lbl_count.setText(f"{len(pids)} piping ID · {n} with a pipe · "
                               f"{len(self.result.get('symbols', []))} symbols")
        self._refresh_review()                      # update badge ⚠ Perlu Review (n)
        self._rebuild_symbol_chips()                # update chip kelas simbol + count
        self._update_tool_state()                   # enable/disable tombol koreksi

    def _search_icon(self):
        """Ikon kaca pembesar digambar sendiri (QPixmap) — pasti tampil walau font tak punya
        emoji 🔍. Lensa lingkaran + gagang diagonal."""
        pm = QtGui.QPixmap(18, 18); pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor("#475569")); pen.setWidth(2)
        p.setPen(pen); p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(2, 2, 9, 9)                    # lensa
        p.drawLine(11, 11, 16, 16)                   # gagang
        p.end()
        return QtGui.QIcon(pm)

    def _toggle_search(self, on):
        """🔍 → munculkan/tutup search bar (seperti Ctrl+F di PDF). Tutup = kosongkan."""
        self.search_bar.setVisible(on)
        if on:
            self.pid_search.setFocus(); self.pid_search.selectAll()
        else:
            self.pid_search.blockSignals(True); self.pid_search.clear()
            self.pid_search.blockSignals(False); self._apply_pid_filter()

    def _on_header_click(self, col):
        """Klik header 'Fluid ▾' / 'Class ▾' -> menu nilai terdeteksi (ala autofilter Excel).
        Pilih 1 nilai -> tabel sisakan baris ber-nilai itu; '(semua)' -> lepas filter kolom."""
        key = {1: "fluid", 2: "pclass"}.get(col)
        if not key or self.result is None:
            return
        pids = self.result["piping_ids"]
        vals = sorted({(p.get(key) or "").strip().upper() for p in pids if (p.get(key) or "").strip()})
        menu = QtWidgets.QMenu(self)
        cur = self._col_filter.get(key)
        a_all = menu.addAction("(all)")
        a_all.setCheckable(True); a_all.setChecked(cur is None)
        menu.addSeparator()
        acts = {}
        for val in vals:
            a = menu.addAction(val); a.setCheckable(True); a.setChecked(cur == val)
            acts[a] = val
        # posisikan menu tepat di bawah header kolom yg diklik
        hdr = self.table.horizontalHeader()
        x = hdr.sectionViewportPosition(col)
        pos = hdr.mapToGlobal(QtCore.QPoint(x, hdr.height()))
        chosen = menu.exec_(pos)
        if chosen is None:
            return
        self._col_filter[key] = None if chosen is a_all else acts.get(chosen, cur)
        # tandai header yg aktif filter -> "Fluid: GR ▾"
        lbl = {"fluid": "Fluid", "pclass": "Class"}[key]
        fv = self._col_filter[key]
        self.table.horizontalHeaderItem(col).setText(f"{lbl}: {fv} ▾" if fv else f"{lbl} ▾")
        self._apply_pid_filter()

    def _apply_pid_filter(self):
        """Sembunyikan baris yang tak cocok: search (substring di pid/fluid/class, ala Ctrl+F)
        AND filter kolom Fluid AND filter kolom Class. GR+CCC -> hanya yg GR&CCC (bila tak ada
        -> kosong)."""
        if not hasattr(self, "table") or self.result is None:
            return
        q = self.pid_search.text().strip().lower() if self.btn_search.isChecked() else ""
        ff = self._col_filter.get("fluid"); fc = self._col_filter.get("pclass")
        pids = self.result["piping_ids"]; shown = 0
        for i in range(self.table.rowCount()):
            p = pids[i] if i < len(pids) else {}
            fluid = (p.get("fluid") or "").upper(); pclass = (p.get("pclass") or "").upper()
            hay = f"{p.get('pid','')} {fluid} {pclass}".lower()
            ok = (not q or q in hay) and (not ff or fluid == ff) and (not fc or pclass == fc)
            self.table.setRowHidden(i, not ok); shown += ok
        if hasattr(self, "lbl_count") and (q or ff or fc):
            n = sum(1 for p in pids if p.get("run_idx", -1) >= 0)
            self.lbl_count.setText(f"{shown}/{len(pids)} piping IDs (filtered) · {n} with a pipe")

    # ---------- interaksi ----------
    def on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        if self.pick_mode:                    # pilih baris = batalkan mode pick/trace yg tertunda
            self._end_mode()
        self.sel_row = rows[0].row(); self.refresh(); self._update_tool_state()
        p = self.result["piping_ids"][self.sel_row]
        self.view.centerOn((p["x1"] + p["x2"]) / 2, (p["y1"] + p["y2"]) / 2)
        self.statusBar().showMessage(f"focus: {p['pid']}")

    def on_canvas_click(self, pt):
        if self.result is None:
            return
        if self.box_edit and not self.pick_mode:      # mode edit kotak: klik kotak -> pilih
            self._pick_box(pt)
            return
        if self.pick_mode == "pipe":
            if self.sel_row < 0:
                return
            runs = self.result["runs"]
            if runs:
                self._snapshot()
                bi = min(range(len(runs)), key=lambda i: _seg_dist(pt.x(), pt.y(), runs[i]))
                self.result["piping_ids"][self.sel_row]["run_idx"] = bi
                self.result["piping_ids"][self.sel_row]["state"] = "manual"; self._set_dirty(True)
            self._end_mode(); self.refresh(); self.statusBar().showMessage("pipe reassigned.")
        elif self.pick_mode == "pidbox":
            self.statusBar().showMessage(
                "click-HOLD then DRAG to form a box (not a plain click) — Esc to cancel.")
        elif self.pick_mode == "trace":
            if self.sel_row < 0:
                return
            self.trace_pts.append([pt.x(), pt.y()]); self._draw_trace_preview()
            self.statusBar().showMessage(f"point {len(self.trace_pts)} — click again, DOUBLE-CLICK to finish.")
        else:                                     # mode biasa -> klik PIPA -> pilih piping ID-nya di list
            self._select_pid_by_pipe(pt)

    def _select_pid_by_pipe(self, pt):
        pids = self.result["piping_ids"]; runs = self.result["runs"]
        best_d, best_r = 1e9, -1
        for r, p in enumerate(pids):
            ri = p.get("run_idx", -1)
            if 0 <= ri < len(runs):
                d = _seg_dist(pt.x(), pt.y(), runs[ri])
                if d < best_d:
                    best_d, best_r = d, r
        if best_r >= 0 and best_d <= 30:
            self.table.selectRow(best_r)          # -> on_select -> fokus ke pipa itu

    # ---------- edit kotak (simbol/equipment/piping ID) adjustable ----------
    def toggle_box_edit(self):
        """Aktif/nonaktif mode edit kotak: klik kotak -> muncul handle sudut (resize) +
        handle tengah (geser). Semua kotak deteksi jadi bisa dibetulkan."""
        self.box_edit = not self.box_edit
        self.btn_boxedit.setChecked(self.box_edit)
        self._end_mode()
        if not self.box_edit:
            self.box_sel = None
        self.refresh()
        self.statusBar().showMessage(
            "✎ Box edit mode ON — click a box to reveal its handles; drag a corner = resize, drag the centre = move." if self.box_edit
            else "box edit mode off.")

    def _boxes_at(self, x, y):
        """List (kind, idx, luas) kotak yang memuat titik (x,y). Terkecil dulu (lebih
        mudah memilih kotak kecil yang tumpang-tindih kotak besar)."""
        out = []
        for i, s in enumerate(self.result.get("symbols", [])):
            if s["x1"] <= x <= s["x2"] and s["y1"] <= y <= s["y2"]:
                out.append(("symbol", i, abs((s["x2"]-s["x1"])*(s["y2"]-s["y1"]))))
        for i, p in enumerate(self.result.get("piping_ids", [])):
            if min(p["x1"],p["x2"]) <= x <= max(p["x1"],p["x2"]) and \
               min(p["y1"],p["y2"]) <= y <= max(p["y1"],p["y2"]):
                out.append(("pid", i, abs((p["x2"]-p["x1"])*(p["y2"]-p["y1"]))))
        out.sort(key=lambda t: t[2])
        return out

    def _pick_box(self, pt):
        hits = self._boxes_at(pt.x(), pt.y())
        if not hits:                                   # klik kosong -> lepas seleksi
            self.box_sel = None; self.refresh()
            return self.statusBar().showMessage("box edit mode — click a box.")
        kind, idx, _ = hits[0]
        self.box_sel = (kind, idx); self.refresh()
        nm = (sym_class(self.result["symbols"][idx]) if kind == "symbol"
              else self.result["piping_ids"][idx].get("pid", "piping ID"))
        self.statusBar().showMessage(f"selected: {nm} — drag a corner / the centre to adjust.")

    def _box_dict(self, kind, idx):
        arr = self.result["symbols"] if kind == "symbol" else self.result["piping_ids"]
        return arr[idx] if 0 <= idx < len(arr) else None

    def _clear_edit_items(self):
        for it in self._edit_items:
            if not sip.isdeleted(it) and it.scene() is self.scene:
                self.scene.removeItem(it)
        self._edit_items = []

    def _begin_box_edit(self):
        """Pasang overlay editable (rect terang + 4 handle sudut + 1 handle tengah) pada
        kotak terpilih. Dipanggil ulang tiap refresh agar seleksi bertahan."""
        self._clear_edit_items()
        if not self.box_sel:
            return
        b = self._box_dict(*self.box_sel)
        if b is None:
            self.box_sel = None; return
        self._syncing = False
        rect = QtWidgets.QGraphicsRectItem()
        rect.setPen(QtGui.QPen(HIGHLIGHT, 3)); rect.setZValue(15)
        self.scene.addItem(rect); self._edit_items.append(rect)
        self._edit_rect = rect
        self._edit_ch = []
        for corner in range(4):                        # 0 TL,1 TR,2 BR,3 BL
            h = Handle(0, 0, self._snapshot, self._mk_corner_move(corner))
            self.scene.addItem(h); self._edit_items.append(h); self._edit_ch.append(h)
        self._edit_mh = Handle(0, 0, self._snapshot, self._mk_center_move())
        self._edit_mh.setBrush(QtGui.QBrush(HIGHLIGHT))   # tengah beda warna (geser)
        self.scene.addItem(self._edit_mh); self._edit_items.append(self._edit_mh)
        self._sync_edit_overlay()

    def _sync_edit_overlay(self, skip=None):
        """Posisikan rect + handle sesuai koordinat kotak sekarang. `skip` = index handle
        sudut yg sedang diseret (jangan dipindah, sudah di kursor)."""
        b = self._box_dict(*self.box_sel) if self.box_sel else None
        if b is None:
            return
        x1, x2 = sorted((b["x1"], b["x2"])); y1, y2 = sorted((b["y1"], b["y2"]))
        self._edit_rect.setRect(x1, y1, x2 - x1, y2 - y1)
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        self._syncing = True
        for i, (cx, cy) in enumerate(corners):
            if i != skip:
                self._edit_ch[i].setPos(cx, cy)
        if skip != -1:
            self._edit_mh.setPos((x1 + x2) / 2, (y1 + y2) / 2)
        self._syncing = False

    def _mk_corner_move(self, corner):
        def move(pos):
            if self._syncing or not self.box_sel:
                return
            b = self._box_dict(*self.box_sel)
            x1, x2 = sorted((b["x1"], b["x2"])); y1, y2 = sorted((b["y1"], b["y2"]))
            px, py = pos.x(), pos.y()
            m = 6                                       # ukuran minimum -> cegah kotak terbalik
            if corner in (0, 3):  x1 = min(px, x2 - m)  # sudut kiri -> geser tepi kiri
            else:                 x2 = max(px, x1 + m)  # sudut kanan -> tepi kanan
            if corner in (0, 1):  y1 = min(py, y2 - m)  # sudut atas
            else:                 y2 = max(py, y1 + m)  # sudut bawah
            b["x1"], b["y1"], b["x2"], b["y2"] = x1, y1, x2, y2
            b["manual"] = True
            self._sync_edit_overlay(skip=corner); self._set_dirty(True)
        return move

    def _mk_center_move(self):
        def move(pos):
            if self._syncing or not self.box_sel:
                return
            b = self._box_dict(*self.box_sel)
            x1, x2 = sorted((b["x1"], b["x2"])); y1, y2 = sorted((b["y1"], b["y2"]))
            w, h = x2 - x1, y2 - y1
            b["x1"], b["y1"] = pos.x() - w / 2, pos.y() - h / 2
            b["x2"], b["y2"] = pos.x() + w / 2, pos.y() + h / 2
            b["manual"] = True
            self._sync_edit_overlay(skip=-1); self._set_dirty(True)
        return move

    def add_symbol_box(self):
        """Tambah kotak simbol yang tak terdeteksi (rubber-band). Pilih kelasnya dulu
        (Equipment / Valve / Instrument) — semuanya masuk asset register & jadi data latih."""
        if self.img_bgr is None:
            return self.statusBar().showMessage("open a drawing first.")
        if self.result is None:
            self.result = {"image_path": self.image_path, "dpi": 350, "rot": self.rot, "w": self.img_bgr.shape[1],
                           "h": self.img_bgr.shape[0], "symbols": [], "runs": [], "piping_ids": []}
        items = ["Equipment", "Valve", "Instrument"]
        cls, ok = QtWidgets.QInputDialog.getItem(
            self, "Add symbol box", "Object class:", items, 0, False)
        if not ok:
            return
        name = ""
        if cls == "Equipment":
            name, ok2 = QtWidgets.QInputDialog.getText(
                self, "Equipment name", "Name / tag (optional, e.g. 605-V-201):")
            if not ok2:
                return
        self._end_mode()
        self._pidbox_target = ("symbol", cls.lower(), name.strip())
        self.pick_mode = "pidbox"; self.view.band_on = True
        self.view.viewport().setCursor(QtCore.Qt.CrossCursor)
        self.statusBar().showMessage(
            f"Click-HOLD, then drag a box around {cls} on the drawing (Esc = cancel).")

    def on_canvas_dblclick(self, pt):
        if self.pick_mode != "trace" or self.sel_row < 0:
            return
        if len(self.trace_pts) >= 2:
            self._snapshot()
            run = {"points": [list(q) for q in self.trace_pts], "axis": "manual",
                   "x1": self.trace_pts[0][0], "y1": self.trace_pts[0][1],
                   "x2": self.trace_pts[-1][0], "y2": self.trace_pts[-1][1]}
            self.result["runs"].append(run); p = self.result["piping_ids"][self.sel_row]
            p["run_idx"] = len(self.result["runs"]) - 1; p["state"] = "manual"; self._set_dirty(True)
            self.statusBar().showMessage("manual pipe created & linked.")
        else:
            self.statusBar().showMessage("trace cancelled (needs at least 2 points).")
        self._end_mode(); self.refresh()

    def _draw_trace_preview(self):
        for it in self.trace_items:                      # aman: skip item yg sudah dihapus
            if not sip.isdeleted(it) and it.scene() is self.scene:
                self.scene.removeItem(it)
        self.trace_items = []
        pen = QtGui.QPen(HIGHLIGHT, 3); brush = QtGui.QBrush(HIGHLIGHT)
        for a, b in zip(self.trace_pts, self.trace_pts[1:]):
            self.trace_items.append(self.scene.addLine(a[0], a[1], b[0], b[1], pen))
        for x, y in self.trace_pts:
            self.trace_items.append(self.scene.addEllipse(x - 5, y - 5, 10, 10, pen, brush))

    def _end_mode(self):
        for it in self.trace_items:                          # bersihkan preview trace yg tertunda
            if not sip.isdeleted(it) and it.scene() is self.scene:
                self.scene.removeItem(it)
        self.trace_pts = []; self.trace_items = []; self.pick_mode = None
        self.view.stop_band(); self._pidbox_target = None    # akhiri mode rubber-band
        self.view.viewport().setCursor(QtCore.Qt.ArrowCursor)  # balikkan kursor ke normal
        self._clear_active_tool()                            # padamkan sorotan tombolnya

    def _cancel(self):                                       # tombol Escape -> tutup drawer / batalkan mode
        if self.drawer.isVisible():
            return self._close_drawer()
        if self.pick_mode:
            self._end_mode(); self.refresh(); self.statusBar().showMessage("cancelled.")
        elif self.box_sel:                                   # lepas seleksi kotak edit
            self.box_sel = None; self.refresh(); self.statusBar().showMessage("box selection cleared.")

    def show_all(self):
        self._end_mode()
        # reset semua filter kolom + search -> benar-benar tampilkan SEMUA
        if hasattr(self, "_col_filter"):
            self._col_filter = {"fluid": None, "pclass": None}
            self.table.horizontalHeaderItem(1).setText("Fluid ▾")
            self.table.horizontalHeaderItem(2).setText("Class ▾")
            self.btn_search.setChecked(False)
        self.table.blockSignals(True); self.table.clearSelection(); self.table.blockSignals(False)
        self.sel_row = -1; self.refresh(); self._update_tool_state()
        self.statusBar().showMessage("showing all piping IDs.")

    def pick_pipe(self):
        if self.sel_row < 0:
            return self.statusBar().showMessage("select a piping ID in the table first.")
        self.pick_mode = "pipe"; self.view.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.statusBar().showMessage("click the correct PIPE on the drawing…")

    def trace_manual(self):
        if self.sel_row < 0:
            return self.statusBar().showMessage("select a piping ID in the table first.")
        self.pick_mode = "trace"; self.trace_pts = []
        self.view.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.statusBar().showMessage("DRAW PIPE: click points along the pipe, DOUBLE-CLICK to finish.")

    def _delete_run(self, ri):
        """Hapus run tracing dgn indeks ri + rapikan run_idx semua piping ID."""
        self._snapshot()
        del self.result["runs"][ri]
        for q in self.result["piping_ids"]:
            rq = q.get("run_idx", -1)
            if rq == ri:
                q["run_idx"] = -1; q["state"] = "none"
            elif rq > ri:
                q["run_idx"] = rq - 1
        self._set_dirty(True); self.refresh(); self.statusBar().showMessage("tracing line deleted.")

    def delete_pipe(self):
        if self.sel_row < 0:
            return
        p = self.result["piping_ids"][self.sel_row]; ri = p.get("run_idx", -1)
        if ri is None or ri < 0:
            return self.statusBar().showMessage("this piping ID has no pipe line yet.")
        self._delete_run(ri)

    def on_canvas_rclick(self, pt):
        """Klik kanan: (1) di dalam kotak simbol/equipment -> hapus simbol itu (buang deteksi
        salah, mis. DETAIL box ke-deteksi equipment); (2) dekat tracing line -> hapus run."""
        if self.result is None or self.pick_mode:
            return
        x, y = pt.x(), pt.y()
        syms = self.result.get("symbols", [])
        hit = next((i for i, s in enumerate(syms)
                    if s["x1"] <= x <= s["x2"] and s["y1"] <= y <= s["y2"]), None)
        if hit is not None:
            menu = QtWidgets.QMenu(self)
            act = menu.addAction(f"\U0001F5D1  Delete '{sym_class(syms[hit])}'")
            if menu.exec_(QtGui.QCursor.pos()) == act:
                self._snapshot(); del self.result["symbols"][hit]
                self.box_sel = None                       # indeks bergeser -> lepas seleksi
                self._set_dirty(True); self.refresh()
                self.statusBar().showMessage("symbol / equipment deleted.")
            return
        runs = self.result["runs"]
        if not runs:
            return
        bi = min(range(len(runs)), key=lambda i: _seg_dist(pt.x(), pt.y(), runs[i]))
        if _seg_dist(pt.x(), pt.y(), runs[bi]) > 30:
            return
        menu = QtWidgets.QMenu(self)
        act = menu.addAction("\U0001F5D1  Delete this tracing line")
        if menu.exec_(QtGui.QCursor.pos()) == act:
            self._delete_run(bi)

    def edit_pid(self):
        if self.sel_row < 0:
            return
        from pidcorr.piping_id import parse_tokens
        p = self.result["piping_ids"][self.sel_row]
        text, ok = QtWidgets.QInputDialog.getText(self, "Edit piping ID", "Piping ID:", text=p["pid"])
        if ok and text.strip():
            self._snapshot(); p["pid"] = text.strip(); p.update(parse_tokens(p["pid"]))
            p["manual"] = True; self._set_dirty(True); self.refresh()

    def add_pid(self):
        if self.img_bgr is None:
            return
        if self.result is None:
            self.result = {"image_path": self.image_path, "dpi": 350, "rot": self.rot, "w": self.img_bgr.shape[1],
                           "h": self.img_bgr.shape[0], "symbols": [], "runs": [], "piping_ids": []}
        text, ok = QtWidgets.QInputDialog.getText(self, "Add piping ID", "Piping ID:")
        if not ok or not text.strip():
            return
        # posisi kotak dari USER: klik-tahan-drag (rubber band) di lokasi teks pada gambar
        self._end_mode()
        self._pidbox_target = ("new", text.strip())
        self.pick_mode = "pidbox"; self.view.band_on = True
        self.view.viewport().setCursor(QtCore.Qt.CrossCursor)
        self.statusBar().showMessage(
            "Click-HOLD then DRAG a box over the piping ID text on the drawing (Esc = cancel).")

    def move_pid_box(self):
        """Gambar ulang kotak posisi utk piping ID terpilih (rubber band)."""
        if self.sel_row < 0:
            return self.statusBar().showMessage("select a piping ID in the table first.")
        self._end_mode()
        self._pidbox_target = ("move", self.sel_row)
        self.pick_mode = "pidbox"; self.view.band_on = True
        self.view.viewport().setCursor(QtCore.Qt.CrossCursor)
        self.statusBar().showMessage(
            "Click-HOLD then DRAG a new box over the piping ID text (Esc = cancel).")

    def ocr_pid_box(self):
        """Kotakkan region piping ID tak terdeteksi -> sistem OCR bagian itu saja."""
        if self.img_bgr is None:
            return self.statusBar().showMessage("open a drawing first.")
        if self.result is None:
            self.result = {"image_path": self.image_path, "dpi": 350, "rot": self.rot, "w": self.img_bgr.shape[1],
                           "h": self.img_bgr.shape[0], "symbols": [], "runs": [], "piping_ids": []}
        self._end_mode()
        self._pidbox_target = ("ocr", None)
        self.pick_mode = "pidbox"; self.view.band_on = True
        self.view.viewport().setCursor(QtCore.Qt.CrossCursor)   # kursor + = mode aktif
        self.statusBar().showMessage(
            "\U0001F50D Box-OCR active — click-HOLD, then drag a box around the piping ID text (Esc = cancel).")

    def add_equipment_box(self):
        """Tandai kotak sebagai EQUIPMENT (masuk asset register) — utk equipment kotak yg tak
        tertangkap auto (mis. DRAIN POT / LUBE OIL UNIT / CYLINDER LUBE OIL TANK)."""
        if self.img_bgr is None:
            return self.statusBar().showMessage("open a drawing first.")
        if self.result is None:
            self.result = {"image_path": self.image_path, "dpi": 350, "rot": self.rot, "w": self.img_bgr.shape[1],
                           "h": self.img_bgr.shape[0], "symbols": [], "runs": [], "piping_ids": []}
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Mark as Equipment", "Equipment name / tag (e.g. Drain Pot, 605-V-201):")
        if not ok:
            return
        self._end_mode()
        self._pidbox_target = ("equip", name.strip())
        self.pick_mode = "pidbox"; self.view.band_on = True
        self.view.viewport().setCursor(QtCore.Qt.CrossCursor)
        self.statusBar().showMessage(
            "Click-HOLD, then drag a box around the equipment on the drawing (Esc = cancel).")

    def on_canvas_band(self, rect):
        """Rubber band selesai -> pasang kotak piping ID (baru/pindah/OCR) atau equipment."""
        if self.pick_mode != "pidbox" or not getattr(self, "_pidbox_target", None):
            return
        target = self._pidbox_target
        kind = target[0]; val = target[1] if len(target) > 1 else None
        from pidcorr.piping_id import parse_tokens, _normalize, ocr_region
        if kind == "symbol":
            coarse, name = target[1], (target[2] if len(target) > 2 else "")
            self._snapshot(); self.result.setdefault("symbols", [])
            rec = {"coarse": coarse, "cls": coarse + "_manual", "conf": 1.0,
                   "x1": int(rect.left()), "y1": int(rect.top()),
                   "x2": int(rect.right()), "y2": int(rect.bottom())}
            if name:
                rec["subtype"] = name
            self.result["symbols"].append(rec)
            self._pidbox_target = None; self._end_mode()
            self._set_dirty(True); self.refresh()
            return self.statusBar().showMessage(
                f"✓ {coarse} added — it will become training data when you save.")
        if kind == "equip":
            self._snapshot(); self.result.setdefault("symbols", [])
            self.result["symbols"].append({
                "coarse": "equipment", "cls": "equipment_manual", "conf": 1.0,
                "x1": int(rect.left()), "y1": int(rect.top()),
                "x2": int(rect.right()), "y2": int(rect.bottom()),
                "subtype": val or "Equipment"})
            self._pidbox_target = None; self._end_mode()
            self._set_dirty(True); self.refresh()
            return self.statusBar().showMessage(f"✓ equipment '{val or 'Equipment'}' added to the register.")
        if kind == "ocr":
            self.overlay.set_status("Mengenali teks (OCR)…"); self.overlay.show_over()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            QtWidgets.QApplication.processEvents()
            try:
                text, matched = ocr_region(self.img_bgr, rect.left(), rect.top(),
                                           rect.right(), rect.bottom())
            finally:
                QtWidgets.QApplication.restoreOverrideCursor(); self.overlay.hide()
            self._pidbox_target = None; self._end_mode()
            if not text:
                return self.statusBar().showMessage("no text could be read in that box — try again.")
            # tampilkan hasil bacaan utk dikonfirmasi/dikoreksi user
            text, ok = QtWidgets.QInputDialog.getText(
                self, "Box OCR result",
                ("Read (matches the format ✓)" if matched else "Read (format not recognised)")
                + " — correct it if it is wrong:", text=text)
            if not ok or not text.strip():
                return
            self._snapshot()
            rec = {"pid": text.strip(), "x1": rect.left(), "y1": rect.top(),
                   "x2": rect.right(), "y2": rect.bottom(),
                   "conf": 0, "run_idx": -1, "state": "none", "manual": True}
            sch = (self.result or {}).get("pid_schema")
            from pidcorr.piping_id import apply_example_schema
            rec.update(apply_example_schema(text.strip(), sch) if sch
                       else parse_tokens(_normalize(text.strip())))
            self.result["piping_ids"].append(rec)
            self._set_dirty(True); self.refresh()
            self.table.selectRow(len(self.result["piping_ids"]) - 1)
            return self.statusBar().showMessage(f"✓ piping ID '{text.strip()}' added from box OCR.")
        self._snapshot()
        if kind == "new":
            rec = {"pid": val, "x1": rect.left(), "y1": rect.top(),
                   "x2": rect.right(), "y2": rect.bottom(),
                   "conf": 0, "run_idx": -1, "state": "none", "manual": True}
            rec.update(parse_tokens(_normalize(val)))
            self.result["piping_ids"].append(rec)
            row = len(self.result["piping_ids"]) - 1
            msg = f"piping ID '{val}' added at the box position — link its pipe if needed."
        else:
            p = self.result["piping_ids"][val]
            p["x1"], p["y1"] = rect.left(), rect.top()
            p["x2"], p["y2"] = rect.right(), rect.bottom()
            p["manual"] = True; row = val
            msg = "piping ID box position updated."
        self._pidbox_target = None
        self._end_mode(); self._set_dirty(True); self.refresh()
        self.table.selectRow(row)
        self.statusBar().showMessage("✓ " + msg)

    def delete_pid(self):
        if self.sel_row >= 0:
            self._snapshot(); del self.result["piping_ids"][self.sel_row]; self.sel_row = -1
            self.table.blockSignals(True); self.table.clearSelection(); self.table.blockSignals(False)
            self._set_dirty(True); self.refresh()

    def teach_parsing(self):
        """Ajari parsing dari 1 contoh (untuk format perusahaan yg belum dikenali):
        user tandai token mana = process fluid & piping class -> diterapkan ke SEMUA
        piping ID (skema posisional disimpan di result['pid_schema'])."""
        if self.sel_row < 0:
            return self.statusBar().showMessage("select one piping ID in the table as the example first.")
        from pidcorr.piping_id import pid_tokens, apply_example_schema, remember_schema
        pid = self.result["piping_ids"][self.sel_row]["pid"]
        toks = pid_tokens(pid)
        if len(toks) < 3:
            return self.statusBar().showMessage("the example piping ID is too short to learn from.")

        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("Teach parsing from an example")
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel(
            f"Example: <b>{pid}</b><br>Click the token that is the <b>Process Fluid</b>, "
            "then the <b>Piping Class</b> token.<br>(optional: Unit, Size, Sequence)"))
        # baris chip token
        pick = {"fluid": None, "pclass": None, "unit": None, "size": None, "seq": None}
        order = ["fluid", "pclass", "unit", "size", "seq"]
        labels = {"fluid": "Process Fluid", "pclass": "Piping Class",
                  "unit": "Unit", "size": "Size", "seq": "Sequence"}
        state = {"next": 0}
        row = QtWidgets.QHBoxLayout()
        btns = []
        status = QtWidgets.QLabel("Next: <b>Process Fluid</b>")

        def click_tok(i):
            if state["next"] >= len(order):
                return
            field = order[state["next"]]
            pick[field] = i
            btns[i].setText(f"{toks[i]}\n[{labels[field]}]")
            btns[i].setEnabled(False)
            state["next"] += 1
            status.setText("Next: <b>%s</b>" % (labels[order[state["next"]]]
                           if state["next"] < len(order) else "— (click OK, or skip the rest)"))

        for i, t in enumerate(toks):
            b = QtWidgets.QPushButton(t); b.setObjectName("Chip")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, i=i: click_tok(i))
            row.addWidget(b); btns.append(b)
        lay.addLayout(row); lay.addWidget(status)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); lay.addWidget(bb)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        schema = {f: i for f, i in pick.items() if i is not None}
        if "fluid" not in schema or "pclass" not in schema:
            return self.statusBar().showMessage("mark at least the Process Fluid & Piping Class.")
        self._snapshot()
        self.result["pid_schema"] = schema
        # Skema disimpan GLOBAL, bukan hanya di gambar ini: format penomoran yang sudah
        # diajarkan sekali harus langsung berlaku pada gambar berikutnya dari perusahaan
        # yang sama, sesuai janji tombolnya.
        try:
            ntok = remember_schema(pid, schema)
        except Exception:
            ntok = 0
        n = 0
        for p in self.result["piping_ids"]:
            parsed = apply_example_schema(p["pid"], schema)
            if parsed.get("fluid") or parsed.get("pclass"):
                p.update(parsed); n += 1
        self._set_dirty(True); self.refresh()
        self.statusBar().showMessage(
            f"✓ parsing learned — fluid & class filled in for {n} piping ID"
            + (f"; it will now be applied automatically to any {ntok}-token code on other "
               f"drawings." if ntok else "."))

    # ---------- undo / dirty / save ----------
    def _snapshot(self):
        if self.result is not None:
            self.undo_stack.append(copy.deepcopy(self.result))
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)

    def undo(self):
        # saat sedang GAMBAR PIPA: undo = batalkan TITIK terakhir (bukan seluruh trace)
        if self.pick_mode == "trace" and self.trace_pts:
            self.trace_pts.pop(); self._draw_trace_preview()
            return self.statusBar().showMessage("last point removed.")
        if not self.undo_stack:
            return self.statusBar().showMessage("nothing to undo.")
        self.result = self.undo_stack.pop(); self.sel_row = -1
        self.table.blockSignals(True); self.table.clearSelection(); self.table.blockSignals(False)
        self._set_dirty(True); self.refresh(); self.statusBar().showMessage("last step undone ✓")

    def _set_dirty(self, d):
        self.dirty = d
        if self.result is None:
            self.save_lbl.setText(""); return
        if d:
            self.save_lbl.setText("● Unsaved"); self.save_lbl.setStyleSheet("color:#B45309; font-weight:600;")
        else:
            self.save_lbl.setText("✓ Saved"); self.save_lbl.setStyleSheet("color:#059669; font-weight:600;")

    def save(self):
        if self.result is None:
            return self.statusBar().showMessage("nothing to save yet.")
        pipeline.save_result(self.result, self._result_path()); self._set_dirty(False)
        msg = "✓ Corrections saved: " + os.path.basename(self._result_path())
        # feedback loop: hanya drawing ber-Reviewed yang jadi sampel latih. Menyimpan tanpa
        # menandai Reviewed berarti sebagian besar kotak belum pernah diperiksa manusia, dan
        # melatih dari situ mengajari model mengulangi kesalahannya sendiri.
        try:
            n_box, _ = feedback.export_sample(self.result, self.img_bgr)
            total = feedback.sample_count()
            if n_box:
                msg += f"  ·  \U0001F393 {n_box} boxes → training data ({total} drawing)"
            elif not self.result.get("reviewed"):
                msg += "  ·  not added to training data — tick 'Reviewed' once every box on "\
                       "this drawing has been checked"
            self._refresh_feedback_btn()
        except Exception as e:
            self.statusBar().showMessage(msg + f"  (feedback skipped: {e})"); return
        self.statusBar().showMessage(msg)

    def _refresh_feedback_btn(self):
        if hasattr(self, "btn_feedback"):
            self.btn_feedback.setText(f"\U0001F393 Training data ({feedback.sample_count()})")

    def show_feedback_info(self):
        n = feedback.sample_count()
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Training data from corrections (feedback loop)")
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText(
            f"<b>{n} corrected drawings</b> are already stored as training data in "
            f"<code>data/feedback/</code>.<br><br>"
            "Every time you press <b>Save</b>, all symbol / equipment boxes (corrected "
            "detections plus your manual additions) are exported as labelled training examples. "
            "The more the tool is used and corrected, the more real examples it accumulates.<br><br>"
            "To retrain the equipment detector with this data, run in a terminal:"
            "<br><code>python scripts/train_equip_big.py</code><br>"
            "(the feedback data is merged in automatically). Then run <b>Detect</b> again.")
        box.exec_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "overlay", None) and self.overlay.isVisible():
            self.overlay.setGeometry(self.det_page.rect())


def main():
    app = QtWidgets.QApplication(sys.argv); app.setStyle("Fusion")

    def hook(t, v, tb):
        import traceback; traceback.print_exception(t, v, tb)
        try: QtWidgets.QMessageBox.critical(None, "An error occurred", str(v))
        except Exception: pass
    sys.excepthook = hook

    w = MainWindow(); w.show(); sys.exit(app.exec_())


if __name__ == "__main__":
    main()
