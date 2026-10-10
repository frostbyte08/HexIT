import colorsys
import json
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QSlider, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .extract import extract

EXTS = {".png", ".jpg", ".jpeg", ".webp"}
QSS = """
QWidget{background:#0b0b0c;color:#ececee;font-size:14px}
QPushButton{background:#1d1d20;border:1px solid #2a2a2e;border-radius:9px;padding:8px 14px}
QPushButton:hover{border-color:#8b8b93} QPushButton:checked{border-color:#e8e4d9}
QPushButton#pri{background:#e8e4d9;color:#0b0b0c;border-color:#e8e4d9;font-weight:600}
QSlider::groove:horizontal{height:4px;background:#2a2a2e;border-radius:2px}
QSlider::sub-page:horizontal{background:#e8e4d9;border-radius:2px}
QSlider::handle:horizontal{width:16px;height:16px;margin:-6px 0;border-radius:8px;background:#e8e4d9}
#drop{background:#151517;border:2px dashed #2a2a2e;border-radius:20px}
#drop[over="true"]{border-color:#e8e4d9;background:#1d1d20}
#card{background:#151517;border:1px solid #2a2a2e;border-radius:14px}
#card QLabel,#card QWidget{background:transparent}
QStatusBar{color:#8b8b93} .dim{color:#8b8b93}
"""


def hsl_of(rgb):
    h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    return round(h * 360), round(s * 100), round(l * 100)


def mono(size=11, bold=False):
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(size)
    f.setBold(bold)
    return f


class DropZone(QFrame):
    def __init__(self, on_click):
        super().__init__()
        self.setObjectName("drop")
        self.on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        t = QLabel("Drop an image here")
        t.setStyleSheet("font-size:24px;background:transparent")
        s = QLabel("or click to select one from your device")
        s.setStyleSheet("color:#8b8b93;background:transparent")
        f = QLabel("PNG · JPG · JPEG · WEBP")
        f.setFont(mono(10))
        f.setStyleSheet("color:#8b8b93;background:transparent")
        for w in (t, s, f):
            w.setAlignment(Qt.AlignCenter)
            lay.addWidget(w)

    def mousePressEvent(self, e):
        self.on_click()

    def set_over(self, v):
        self.setProperty("over", v)
        self.style().unpolish(self)
        self.style().polish(self)


class Swatch(QFrame):
    def __init__(self, c, details, on_copy, on_remove):
        super().__init__()
        self.c, self.on_copy = c, on_copy
        r = c["rgb"]
        fg = "#111" if .299 * r[0] + .587 * r[1] + .114 * r[2] > 150 else "#fff"
        self.setObjectName("sw")
        self.setStyleSheet(f"#sw{{background:{c['hex']}}} QLabel{{color:{fg};background:transparent}}")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(72)
        self.setToolTip(f"Click to copy {c['hex']}")
        lay = QVBoxLayout(self)
        self.x = QToolButton()
        self.x.setText("✕")
        self.x.setStyleSheet("QToolButton{background:rgba(0,0,0,110);color:#fff;border-radius:12px;min-width:24px;min-height:24px}")
        self.x.clicked.connect(lambda: on_remove(c))
        self.x.hide()
        lay.addWidget(self.x, 0, Qt.AlignRight)
        lay.addStretch()
        h = hsl_of(r)
        self.det = QLabel(f"rgb({r[0]}, {r[1]}, {r[2]})\nhsl({h[0]}, {h[1]}%, {h[2]}%)")
        self.det.setFont(mono(9))
        self.hexl = QLabel(c["hex"])
        self.hexl.setFont(mono(12, True))
        pc = QLabel(f"{c['pct']:.1f}%")
        pc.setFont(mono(9))
        for w in (self.det, self.hexl, pc):
            lay.addWidget(w)
        self.details = details
        self.det.setVisible(details)

    def enterEvent(self, e):
        self.x.show()
        self.det.show()

    def leaveEvent(self, e):
        self.x.hide()
        self.det.setVisible(self.details)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_copy(self.c["hex"])


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chromatic — Image to palette")
        self.resize(1100, 680)
        self.setAcceptDrops(True)
        self.pix = self.rgba = None
        self.pal, self.seed, self.name = [], 1, "palette"
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.drop = DropZone(self.open_dialog)
        page0 = QWidget()
        l0 = QVBoxLayout(page0)
        l0.setContentsMargins(28, 28, 28, 28)
        l0.addWidget(self.title())
        l0.addWidget(self.drop, 1)
        self.stack.addWidget(page0)
        page1 = QWidget()
        l1 = QVBoxLayout(page1)
        l1.setContentsMargins(28, 28, 28, 28)
        l1.addWidget(self.title())
        body = QHBoxLayout()
        l1.addLayout(body, 1)
        # left: image card
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(340)
        cl = QVBoxLayout(card)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(10, 10)
        self.fn, self.dim = QLabel(), QLabel()
        self.fn.setFont(mono(10, True))
        self.dim.setFont(mono(9))
        self.dim.setProperty("class", "dim")
        self.dim.setStyleSheet("color:#8b8b93")
        rep, rm = QPushButton("Replace"), QPushButton("Remove")
        rep.clicked.connect(self.open_dialog)
        rm.clicked.connect(self.reset)
        br = QHBoxLayout()
        br.addWidget(rep)
        br.addWidget(rm)
        for w in (self.preview,):
            cl.addWidget(w, 1)
        cl.addWidget(self.fn)
        cl.addWidget(self.dim)
        cl.addLayout(br)
        body.addWidget(card)
        # right: controls + palette + exports
        right = QVBoxLayout()
        body.addLayout(right, 1)
        ctl = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(3, 10)
        self.slider.setValue(6)
        self.nl = QLabel("6")
        self.nl.setFont(mono(16, True))
        self.slider.valueChanged.connect(self.on_n)
        regen = QPushButton("Regenerate")
        regen.clicked.connect(self.regen)
        self.detb = QPushButton("Details")
        self.detb.setCheckable(True)
        self.detb.toggled.connect(lambda _: self.draw())
        for w in (QLabel("Colors"), self.slider, self.nl, regen, self.detb):
            ctl.addWidget(w, 1 if w is self.slider else 0)
        right.addLayout(ctl)
        self.palw = QWidget()
        self.pall = QHBoxLayout(self.palw)
        self.pall.setContentsMargins(0, 0, 0, 0)
        self.pall.setSpacing(6)
        right.addWidget(self.palw, 1)
        ex = QHBoxLayout()
        for text, fn, pri in (("Copy all HEX", self.copy_all, True), ("PNG", self.exp_png, False),
                              ("JSON", self.exp_json, False), ("CSS", self.exp_css, False),
                              ("Reset", self.reset, False)):
            b = QPushButton(text)
            if pri:
                b.setObjectName("pri")
            b.clicked.connect(fn)
            ex.addWidget(b)
        ex.addStretch()
        right.addLayout(ex)
        self.stack.addWidget(page1)

    def title(self):
        w = QLabel('<span style="font-size:20px;font-weight:600">◐ Chromatic</span>'
                   '  <span style="color:#8b8b93">Image to palette</span>')
        return w

    def toast(self, m):
        self.statusBar().showMessage(m, 2000)

    # ---- loading
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
        img = r.read()
        if img.isNull():
            return self.toast("Could not read image")
        self.pix, self.name = QPixmap.fromImage(img), p.stem
        self.fn.setText(p.name)
        self.dim.setText(f"{img.width()} × {img.height()} px")
        if max(img.width(), img.height()) > 160:  # processing copy; original untouched
            img = img.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        buf = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())
        self.rgba = buf[:, :w * 4].reshape(h, w, 4).copy()
        self.seed = 1
        self.stack.setCurrentIndex(1)
        self.fit()
        self.run()

    def fit(self):
        if self.pix:
            self.preview.setPixmap(self.pix.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()

    def reset(self):
        self.pix = self.rgba = None
        self.pal = []
        self.preview.clear()
        self.stack.setCurrentIndex(0)

    # ---- palette
    def run(self):
        if self.rgba is None:
            return
        self.pal = extract(self.rgba, self.slider.value(), self.seed)
        for c in self.pal:
            c["hex"] = "#%02X%02X%02X" % c["rgb"]
        self.draw()

    def on_n(self, v):
        self.nl.setText(str(v))
        self.run()

    def regen(self):
        self.seed += 1
        self.run()

    def draw(self):
        while (it := self.pall.takeAt(0)):
            if it.widget():
                it.widget().deleteLater()
        if not self.pal:
            self.pall.addWidget(QLabel("No colors left — press Regenerate"), 0, Qt.AlignCenter)
        for c in self.pal:
            self.pall.addWidget(Swatch(c, self.detb.isChecked(), self.copy, self.remove), max(3, round(c["pct"])))

    def remove(self, c):
        self.pal.remove(c)
        self.draw()

    def copy(self, text):
        QGuiApplication.clipboard().setText(text)
        self.toast("Copied " + (text if "\n" not in text else "all HEX values"))

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
            self.drop.set_over(True)

    def dragLeaveEvent(self, e):
        self.drop.set_over(False)

    def dropEvent(self, e):
        self.drop.set_over(False)
        u = self._urls(e)
        if u:
            self.load(u[0])


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Chromatic")
    app.setStyleSheet(QSS)
    w = Window()
    w.show()
    if len(sys.argv) > 1:
        w.load(sys.argv[1])
    sys.exit(app.exec())
