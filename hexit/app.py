import colorsys
import json
import sys
from functools import partial
from pathlib import Path

import numpy as np
from PySide6.QtCore import (QEasingCurve, QObject, QPropertyAnimation, QRect, QRunnable,
                            QSequentialAnimationGroup, QSize, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QColor, QFontDatabase, QFontMetrics, QGuiApplication, QImage, QImageReader,
                           QKeySequence, QPainter, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QBoxLayout, QFileDialog, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QMainWindow, QPushButton, QSizePolicy, QSlider,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .extract import cluster, prepare

EXTS = {".png", ".jpg", ".jpeg", ".webp"}
PREVIEW_MAX, WORK_MAX = 1200, 160
QSS = """
QWidget{background:#0b0b0c;color:#ececee;font-size:13px}
QLabel{background:transparent}
QPushButton{background:transparent;border:1px solid #26262a;border-radius:9px;padding:8px 14px}
QPushButton:hover{background:#1a1a1d;border-color:#3a3a40}
QPushButton:pressed{background:#222226}
QPushButton:focus{border-color:#8b8b93}
QPushButton:checked{background:#1d1d20;border-color:#e8e4d9}
QPushButton#pri{background:#e8e4d9;color:#0b0b0c;border-color:#e8e4d9;font-weight:600}
QPushButton#pri:hover{background:#fff}
QPushButton#ghost{border-color:transparent;color:#8b8b93}
QPushButton#ghost:hover{color:#ececee;background:#151517}
QSlider::groove:horizontal{height:4px;background:#26262a;border-radius:2px}
QSlider::sub-page:horizontal{background:#e8e4d9;border-radius:2px}
QSlider::handle:horizontal{width:16px;height:16px;margin:-6px 0;border-radius:8px;background:#e8e4d9}
QSlider::handle:horizontal:hover{background:#fff}
#drop{background:#111113;border:2px dashed #2a2a2e;border-radius:20px}
#card{background:#131315;border:1px solid #26262a;border-radius:14px}
#rule{background:#1c1c1f}
"""


def hsl_of(rgb):
    h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    return round(h * 360), round(s * 100), round(l * 100)


def mono(size=11, bold=False):
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(size)
    f.setBold(bold)
    return f


def dim(text, size=9):
    l = QLabel(text)
    l.setFont(mono(size))
    l.setStyleSheet("color:#8b8b93")
    return l


def fade(w, a, b, ms=220, delay=0, done=None):
    """Opacity animation that is safe if the widget is deleted mid-flight."""
    eff = w.graphicsEffect()
    if not isinstance(eff, QGraphicsOpacityEffect):
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
    eff.setOpacity(a)
    g = QSequentialAnimationGroup(w)
    if delay:
        g.addPause(delay)
    an = QPropertyAnimation(eff, b"opacity")
    an.setDuration(ms)
    an.setStartValue(a)
    an.setEndValue(b)
    an.setEasingCurve(QEasingCurve.OutCubic)
    g.addAnimation(an)
    if done:
        g.finished.connect(done)
    g.start(QSequentialAnimationGroup.DeleteWhenStopped)


class Sig(QObject):
    done = Signal(int, object)


class Job(QRunnable):
    """Clustering runs off the UI thread; stale results are dropped by job id."""
    def __init__(self, jid, prep, k, seed, sig):
        super().__init__()
        self.args, self.sig = (jid, prep, k, seed), sig

    def run(self):
        jid, prep, k, seed = self.args
        self.sig.done.emit(jid, cluster(prep, k, seed))


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("background:#e8e4d9;color:#0b0b0c;border-radius:15px;padding:7px 16px")
        self.setFont(mono(10))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.eff = QGraphicsOpacityEffect(self)
        self.eff.setOpacity(0)
        self.setGraphicsEffect(self.eff)
        self.anim = QPropertyAnimation(self.eff, b"opacity", self)
        self.anim.setDuration(180)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._to(0))
        self.hide()

    def _to(self, v):
        self.anim.stop()
        self.anim.setStartValue(self.eff.opacity())
        self.anim.setEndValue(v)
        self.anim.start()

    def say(self, msg):
        self.setText(msg)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 84)
        self.show()
        self.raise_()
        self._to(1)
        self.timer.start(1800)


class DropZone(QFrame):
    def __init__(self, on_click):
        super().__init__()
        self.setObjectName("drop")
        self.on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(8)
        t = QLabel("Drop an image here")
        t.setStyleSheet("font-size:26px;font-weight:300")
        s = QLabel("or click to select one  ·  Ctrl+V to paste")
        s.setStyleSheet("color:#8b8b93")
        for w in (t, s, dim("PNG · JPG · JPEG · WEBP", 10)):
            w.setAlignment(Qt.AlignCenter)
            lay.addWidget(w)

    def mousePressEvent(self, e):
        self.on_click()


class Swatch(QFrame):
    def __init__(self, c, details, on_copy, on_remove):
        super().__init__()
        self.c, self.on_copy, self.on_remove, self.details, self.gone = c, on_copy, on_remove, details, False
        r = c["rgb"]
        fg = "#111" if .299 * r[0] + .587 * r[1] + .114 * r[2] > 150 else "#fff"
        self.setObjectName("sw")
        self.setStyleSheet(f"#sw{{background:{c['hex']};border-radius:10px}} QLabel{{color:{fg};background:transparent}}")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(64, 52)
        self.setToolTip(f"Click to copy {c['hex']}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.setSpacing(2)
        self.x = QToolButton()
        self.x.setText("✕")
        self.x.setCursor(Qt.ArrowCursor)
        self.x.setStyleSheet("QToolButton{background:rgba(0,0,0,120);color:#fff;border:0;border-radius:12px;"
                             "min-width:24px;min-height:24px}QToolButton:hover{background:rgba(0,0,0,190)}")
        self.x.clicked.connect(lambda: on_remove(self))
        self.x.hide()
        lay.addWidget(self.x, 0, Qt.AlignRight)
        lay.addStretch()
        self.det = QLabel(f"rgb({r[0]}, {r[1]}, {r[2]})\nhsl({hsl_of(r)[0]}, {hsl_of(r)[1]}%, {hsl_of(r)[2]}%)")
        self.det.setFont(mono(9))
        self.hexl = QLabel(c["hex"])
        self.hexl.setFont(mono(12, True))
        pc = QLabel(f"{c['pct']:.1f}%")
        pc.setFont(mono(9))
        for w in (self.det, self.hexl, pc):
            lay.addWidget(w)
        self.det.setVisible(details)
        self.tm = QTimer(self)
        self.tm.setSingleShot(True)
        self.tm.timeout.connect(lambda: self.hexl.setText(self.c["hex"]))

    def set_details(self, v):
        self.details = v
        self.det.setVisible(v or self.underMouse())

    def flash(self):
        self.hexl.setText("Copied")
        self.tm.start(900)

    def enterEvent(self, e):
        self.x.show()
        self.det.show()

    def leaveEvent(self, e):
        self.x.hide()
        self.det.setVisible(self.details)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_copy(self.c["hex"], self)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chromatic — Image to palette")
        self.resize(1120, 700)
        self.setMinimumSize(760, 520)
        self.setAcceptDrops(True)
        self.pix = self.prep = None
        self.pal, self.swatches, self.seed, self.jid, self.name = [], [], 1, 0, "palette"
        self.pool, self.sig = QThreadPool.globalInstance(), Sig()
        self.sig.done.connect(self.on_done)
        self.deb = QTimer(self, singleShot=True, interval=60)  # debounce slider drags
        self.deb.timeout.connect(self.submit)
        self.rt = QTimer(self, singleShot=True, interval=120)  # smooth rescale after resize settles
        self.rt.timeout.connect(lambda: self.fit(False))

        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(32, 22, 32, 28)
        rl.setSpacing(0)
        head = QHBoxLayout()
        logo = QLabel("◐ Chromatic")
        logo.setStyleSheet("font-size:19px;font-weight:600")
        head.addWidget(logo)
        head.addSpacing(10)
        head.addWidget(dim("Image to palette", 10))
        head.addStretch()
        head.addWidget(dim("Ctrl+O open · Ctrl+V paste · R regenerate · D details", 9))
        rl.addLayout(head)
        rl.addSpacing(16)
        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFixedHeight(1)
        rl.addWidget(rule)
        rl.addSpacing(22)
        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.stack.addWidget(DropZone(self.open_dialog))
        page = QWidget()
        body = QHBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(24)
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(320)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 14, 14, 14)
        cl.setSpacing(6)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.fn, self.dimlbl = QLabel(), dim("")
        self.fn.setFont(mono(10, True))
        rep, rm = QPushButton("Replace"), QPushButton("Remove")
        rep.clicked.connect(self.open_dialog)
        rm.clicked.connect(self.reset)
        br = QHBoxLayout()
        br.addWidget(rep)
        br.addWidget(rm)
        cl.addWidget(self.preview, 1)
        cl.addSpacing(6)
        cl.addWidget(self.fn)
        cl.addWidget(self.dimlbl)
        cl.addSpacing(6)
        cl.addLayout(br)
        body.addWidget(card)

        right = QVBoxLayout()
        right.setSpacing(18)
        body.addLayout(right, 1)
        ctl = QHBoxLayout()
        ctl.setSpacing(12)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(3, 10)
        self.slider.setValue(6)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.nl = QLabel("6")
        self.nl.setFont(mono(16, True))
        self.nl.setMinimumWidth(26)
        self.slider.valueChanged.connect(self.on_n)
        regen = QPushButton("Regenerate")
        regen.setToolTip("R")
        regen.clicked.connect(self.regen)
        self.detb = QPushButton("Details")
        self.detb.setToolTip("D")
        self.detb.setCheckable(True)
        self.detb.toggled.connect(lambda v: [s.set_details(v) for s in self.swatches])
        ctl.addWidget(dim("COLORS", 9))
        ctl.addWidget(self.slider, 1)
        ctl.addWidget(self.nl)
        ctl.addSpacing(8)
        ctl.addWidget(regen)
        ctl.addWidget(self.detb)
        right.addLayout(ctl)
        self.palw = QWidget()
        self.pall = QBoxLayout(QBoxLayout.LeftToRight, self.palw)
        self.pall.setContentsMargins(0, 0, 0, 0)
        self.pall.setSpacing(8)
        right.addWidget(self.palw, 1)
        ex = QHBoxLayout()
        ex.setSpacing(8)
        a = QPushButton("Copy all HEX")
        a.setObjectName("pri")
        a.setToolTip("Ctrl+Shift+C")
        a.clicked.connect(self.copy_all)
        ex.addWidget(a)
        ex.addStretch()
        ex.addWidget(dim("EXPORT", 9))
        for text, fn in (("PNG", self.exp_png), ("JSON", self.exp_json), ("CSS", self.exp_css)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            ex.addWidget(b)
        ex.addSpacing(8)
        rs = QPushButton("Reset")
        rs.setObjectName("ghost")
        rs.clicked.connect(self.reset)
        ex.addWidget(rs)
        right.addLayout(ex)
        self.stack.addWidget(page)

        self.toast_w = Toast(self)
        self.veil = QLabel("Drop image to open", self)
        self.veil.setAlignment(Qt.AlignCenter)
        self.veil.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.veil.setStyleSheet("background:rgba(11,11,12,230);border:2px dashed #e8e4d9;border-radius:20px;font-size:22px")
        self.veil.hide()
        for key, fn in (("Ctrl+O", self.open_dialog), ("Ctrl+V", self.paste), ("R", self.regen),
                        ("D", self.detb.toggle), ("Ctrl+Shift+C", self.copy_all)):
            QShortcut(QKeySequence(key), self).activated.connect(fn)

    def toast(self, m):
        self.toast_w.say(m)

    # ---- loading (fast path: decode a reduced copy, never touch the original)
    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select image", str(Path.home()),
                                           "Images (*.png *.jpg *.jpeg *.webp)")
        if p:
            self.load(p)

    def load(self, path):
        p = Path(path)
        if p.suffix.lower() not in EXTS:
            return self.toast("Use PNG, JPG or WEBP")
        r = QImageReader(str(p))
        r.setAutoTransform(True)
        sz = r.size()
        if sz.isValid() and max(sz.width(), sz.height()) > PREVIEW_MAX:
            s = PREVIEW_MAX / max(sz.width(), sz.height())
            r.setScaledSize(QSize(max(1, round(sz.width() * s)), max(1, round(sz.height() * s))))
        img = r.read()
        if img.isNull():
            return self.toast("Could not read image")
        ow, oh = (sz.width(), sz.height()) if sz.isValid() else (img.width(), img.height())
        if sz.isValid() and sz.width() != sz.height() and (sz.width() > sz.height()) != (img.width() > img.height()):
            ow, oh = oh, ow  # EXIF-rotated
        self.ingest(img, p.name, p.stem, ow, oh)

    def paste(self):
        md = QGuiApplication.clipboard().mimeData()
        urls = [u.toLocalFile() for u in md.urls() if Path(u.toLocalFile()).suffix.lower() in EXTS] if md.hasUrls() else []
        if urls:
            self.load(urls[0])
        elif md.hasImage():
            img = QGuiApplication.clipboard().image()
            if not img.isNull():
                self.ingest(img, "Pasted image", "pasted", img.width(), img.height())
        else:
            self.toast("No image in clipboard")

    def ingest(self, img, fname, stem, ow, oh):
        self.pix, self.name = QPixmap.fromImage(img), stem
        self.fn.setText(QFontMetrics(self.fn.font()).elidedText(fname, Qt.ElideMiddle, 280))
        self.fn.setToolTip(fname)
        self.dimlbl.setText(f"{ow} × {oh} px")
        if max(img.width(), img.height()) > WORK_MAX:
            img = img.scaled(WORK_MAX, WORK_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        buf = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())
        self.prep = prepare(buf[:, :w * 4].reshape(h, w, 4).copy())  # bucketed once; reused for every k
        self.seed = 1
        self.stack.setCurrentIndex(1)
        QTimer.singleShot(0, self.fit)
        self.submit()

    def fit(self, fast=False):
        if self.pix and self.preview.width() > 10:
            self.preview.setPixmap(self.pix.scaled(self.preview.size(), Qt.KeepAspectRatio,
                                                   Qt.FastTransformation if fast else Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.pall.setDirection(QBoxLayout.LeftToRight if self.width() > 980 else QBoxLayout.TopToBottom)
        self.veil.setGeometry(self.rect().adjusted(14, 14, -14, -14))
        self.fit(True)
        self.rt.start()

    def reset(self):
        self.pix = self.prep = None
        self.jid += 1
        self.pal = []
        self.preview.clear()
        self.stack.setCurrentIndex(0)

    # ---- palette (async)
    def submit(self):
        if self.prep is None:
            return
        self.jid += 1
        self.pool.start(Job(self.jid, self.prep, self.slider.value(), self.seed, self.sig))

    def on_done(self, jid, res):
        if jid == self.jid:
            self.pal = res
            self.draw()

    def on_n(self, v):
        self.nl.setText(str(v))
        self.deb.start()

    def regen(self):
        if self.prep is not None:
            self.seed += 1
            self.submit()

    def draw(self, animate=True):
        while (it := self.pall.takeAt(0)):
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        self.swatches = []
        if not self.pal:
            e = QLabel("No colors left — press R to regenerate")
            e.setAlignment(Qt.AlignCenter)
            e.setStyleSheet("color:#8b8b93")
            self.pall.addWidget(e)
            return
        for i, c in enumerate(self.pal):
            s = Swatch(c, self.detb.isChecked(), self.copy, self.remove)
            self.swatches.append(s)
            self.pall.addWidget(s, max(3, round(c["pct"])))
            if animate:
                fade(s, 0, 1, 260, i * 35)

    def remove(self, sw):
        if sw.gone:
            return
        sw.gone = True
        fade(sw, 1, 0, 160, done=partial(self._removed, sw))

    def _removed(self, sw):
        if sw.c in self.pal:
            self.pal.remove(sw.c)
        if sw in self.swatches:
            self.swatches.remove(sw)
        if self.pal:
            self.pall.removeWidget(sw)
            sw.deleteLater()
        else:
            self.draw(False)

    def copy(self, text, sw=None):
        QGuiApplication.clipboard().setText(text)
        sw.flash() if sw else self.toast("Copied all HEX values")

    def copy_all(self):
        if self.pal:
            self.copy("\n".join(c["hex"] for c in self.pal))

    # ---- exports (all local)
    def ask(self, ext, filt):
        p, _ = QFileDialog.getSaveFileName(self, "Save", str(Path.home() / f"{self.name}-palette.{ext}"), filt)
        return p

    def exp_json(self):
        p = self.pal and self.ask("json", "JSON (*.json)")
        if p:
            data = [{"hex": c["hex"], "rgb": dict(zip("rgb", c["rgb"])), "hsl": dict(zip("hsl", hsl_of(c["rgb"])))}
                    for c in self.pal]
            Path(p).write_text(json.dumps(data, indent=2))
            self.toast("Saved " + Path(p).name)

    def exp_css(self):
        p = self.pal and self.ask("css", "CSS (*.css)")
        if p:
            body = "\n".join(f"  --color-{i}: {c['hex']};" for i, c in enumerate(self.pal, 1))
            Path(p).write_text(f":root {{\n{body}\n}}\n")
            self.toast("Saved " + Path(p).name)

    def exp_png(self):
        p = self.pal and self.ask("png", "PNG (*.png)")
        if not p:
            return
        n = len(self.pal)
        W, H = max(600, n * 160), 360
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(QColor("#0b0b0c"))
        q = QPainter(img)
        q.setFont(mono(14, True))
        w = W // n
        for i, c in enumerate(self.pal):
            q.fillRect(QRect(i * w, 0, w, H - 70), QColor(c["hex"]))
            q.setPen(QColor("#ececee"))
            q.drawText(QRect(i * w, H - 70, w, 70), Qt.AlignCenter, c["hex"])
        q.end()
        img.save(p)
        self.toast("Saved " + Path(p).name)

    # ---- drag & drop
    def _urls(self, e):
        return [u.toLocalFile() for u in e.mimeData().urls() if Path(u.toLocalFile()).suffix.lower() in EXTS]

    def dragEnterEvent(self, e):
        if self._urls(e):
            e.acceptProposedAction()
            self.veil.show()
            self.veil.raise_()

    def dragLeaveEvent(self, e):
        self.veil.hide()

    def dropEvent(self, e):
        self.veil.hide()
        u = self._urls(e)
        if u:
            self.load(u[0])


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("HexIT")
    app.setStyleSheet(QSS)
    w = Window()
    w.show()
    if len(sys.argv) > 1:
        w.load(sys.argv[1])
    sys.exit(app.exec())
