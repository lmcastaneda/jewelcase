"""The poster grid: the same games as the list, shown by their covers. And the banner that heads a
system's page with the system's image."""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QImageReader, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout, QLabel, QListView,
                               QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout)

from ..core import systems
from ..core.fmt import human_size, plural
from . import models, style
from .widgets import draw_check
from .workers import run_async

COVER_W, COVER_H = 140, 196             # the box a cover is fitted into
TILE_W, TILE_H = 158, 250               # one game in the grid: the cover, then two lines of text
LIST_W, LIST_H = 22, 30                 # the box a small cover in the list is fitted into
SYSTEM_SIDE = 72                        # a system's image in the banner
KEPT = 320                              # pictures kept ready to draw; the ones not seen longest go first


def _read(path: str, width: int, height: int) -> QImage | None:
    """A picture scaled down to fit the box, or None when the file is not a picture."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > width or size.height() > height):
        reader.setScaledSize(size.scaled(width, height, Qt.KeepAspectRatio))
    image = reader.read()
    return None if image.isNull() else image


def fit_title(title: str, font: QFont, width: int, height: int) -> str:
    """The title as it fits a card when wrapped: all of it, or its beginning and an ellipsis."""
    metrics = QFontMetrics(font)
    box = QRect(0, 0, width, 10_000)

    def fits(text: str) -> bool:
        return metrics.boundingRect(box, Qt.AlignCenter | Qt.TextWordWrap, text).height() <= height
    if fits(title):
        return title
    low, high = 0, len(title)                   # the longest beginning that still fits with the ellipsis
    while low < high:
        middle = (low + high + 1) // 2
        if fits(title[:middle].rstrip() + "…"):
            low = middle
        else:
            high = middle - 1
    return title[:low].rstrip() + "…"


class PosterCache(QObject):
    """Pictures ready to draw. One that is not loaded yet is read off the UI thread; `changed` says when
    there is something new to paint."""

    changed = Signal()

    def __init__(self, art):
        super().__init__()
        self._art = art                 # a function giving the core.art.Art to read from
        # key -> (the file it was read from, picture or None when the file is not a picture), oldest use first
        self._ready: OrderedDict[tuple, tuple[tuple, QPixmap | None]] = OrderedDict()
        self._loading: set[tuple] = set()
        self.limit = KEPT
        self.reads = 0                  # files read so far
        self._soon = QTimer(self)
        self._soon.setSingleShot(True)
        self._soon.setInterval(40)      # many pictures arriving together cause one repaint
        self._soon.timeout.connect(self.changed)

    def pixmap(self, kind: str, system: str, stem: str = "") -> QPixmap | None:
        """The cover of a game (kind "cover") or the image of a system ("system"). None when there is none,
        when it is not loaded yet, or when the file is not a picture."""
        art = self._art()
        path = art.cover(system, stem) if kind == "cover" else art.system(system)
        if path is None:
            return None
        try:
            st = path.stat()
        except OSError:
            return None
        stamp = (str(path), st.st_mtime_ns, st.st_size)
        key = (kind, system, stem)
        have = self._ready.get(key)
        if have:
            self._ready.move_to_end(key)
        if have and have[0] == stamp:
            return have[1]
        if key not in self._loading:
            self._loading.add(key)
            self.reads += 1
            box = (2 * COVER_W, 2 * COVER_H) if kind == "cover" else (2 * SYSTEM_SIDE, 2 * SYSTEM_SIDE)
            run_async(lambda: _read(stamp[0], *box), lambda image: self._loaded(key, stamp, image),
                      lambda error: self._loaded(key, stamp, None))
        return have[1] if have else None        # the picture it had stays until the new one is read

    def _loaded(self, key: tuple, stamp: tuple, image: QImage | None) -> None:
        self._loading.discard(key)
        self._ready[key] = (stamp, QPixmap.fromImage(image) if image is not None else None)
        self._ready.move_to_end(key)
        while len(self._ready) > self.limit:
            self._ready.popitem(last=False)
        self._soon.start()

    def settled(self, kind: str, system: str, stem: str = "") -> bool:
        """Whether this picture has been read (it may have turned out not to be a picture)."""
        return (kind, system, stem) in self._ready and (kind, system, stem) not in self._loading

    def kept(self) -> int:
        return len(self._ready)


def games_key(window, view, event) -> bool:
    """Keys the list and the grid share: Space ticks the selection, typing starts a search."""
    if event.key() == Qt.Key_Space and view.selectionModel().hasSelection():
        window.toggle_selection()
        return True
    typed = event.text()
    plain = not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
    if plain and typed and typed.isprintable() and not typed.isspace():
        search = window.search
        search.setFocus()
        search.setText(search.text() + typed)
        return True
    return False


class PosterDelegate(QStyledItemDelegate):
    def __init__(self, cache: PosterCache, parent=None):
        super().__init__(parent)
        self.cache = cache

    def sizeHint(self, option, index) -> QSize:
        return QSize(TILE_W, TILE_H)

    @staticmethod
    def cover_rect(rect: QRect) -> QRect:
        return QRect(rect.x() + (rect.width() - COVER_W) // 2, rect.y() + 8, COVER_W, COVER_H)

    def check_rect(self, rect: QRect) -> QRect:
        """Where the tick box of a tile is: under the cover, in front of the title, so the art stays whole."""
        cover = self.cover_rect(rect)
        return QRect(cover.x() - 2, cover.bottom() + 4, 22, 22)

    @staticmethod
    def lines(row) -> tuple[str, str]:
        """The two lines under a cover: the game, and what the list's Status column says about it."""
        return row.title, models.status_text(row)

    def paint(self, painter: QPainter, option, index) -> None:
        row = index.data(models.ROW_ROLE)
        if row is None:
            return
        t = style.tokens()
        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if selected or option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t["sel"] if selected else t["alt"]))
            painter.drawRoundedRect(QRectF(rect).adjusted(3, 3, -3, -3), 8, 8)

        cover = QRectF(self.cover_rect(rect))
        pix = self.cache.pixmap("cover", row.system, row.stem)
        painter.setOpacity(1.0 if row.on_device or row.want else 0.5)     # not on the handheld: paler
        if pix is not None:
            size = QSize(pix.size()).scaled(COVER_W, COVER_H, Qt.KeepAspectRatio)
            shown = QRectF(0, 0, size.width(), size.height())
            shown.moveCenter(cover.center())
            shown.moveBottom(cover.bottom())
            clip = QPainterPath()
            clip.addRoundedRect(shown, 5, 5)
            painter.setClipPath(clip)
            painter.drawPixmap(shown, pix, QRectF(pix.rect()))
            painter.setClipping(False)
            painter.setPen(QPen(QColor(0, 0, 0, 50), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(shown.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        else:
            self._placeholder(painter, cover, row)
        painter.setOpacity(1.0)

        box = QRectF(self.check_rect(rect))
        mark = QRectF(0, 0, 14, 14)
        mark.moveCenter(box.center())
        checkable = bool(index.siblingAtColumn(models.COL_CHECK).flags() & Qt.ItemIsUserCheckable)
        draw_check(painter, mark, row.want, checkable, selected)

        title, status = self.lines(row)
        font = QFont(option.font)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        left = int(box.right()) + 3
        width = int(cover.right()) - left
        line = QRect(left, int(box.center().y()) - metrics.height() // 2, width, metrics.height())
        painter.setPen(QColor(t["sel_text"] if selected else t["text"]))
        painter.drawText(line, Qt.AlignLeft | Qt.AlignVCenter, metrics.elidedText(title, Qt.ElideRight, width))
        small = QFont(font)
        small.setPointSizeF(max(7.0, font.pointSizeF() * 0.88))
        painter.setFont(small)
        colour = index.siblingAtColumn(models.COL_STATUS).data(Qt.ForegroundRole)
        painter.setPen(QColor(t["sel_text"]) if selected else (colour or QColor(t["muted"])))
        painter.drawText(QRect(left, line.bottom() + 2, width, QFontMetrics(small).height()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(small).elidedText(status, Qt.ElideRight, width))
        painter.restore()

    @staticmethod
    def _placeholder(painter: QPainter, cover: QRectF, row) -> None:
        """A game without a cover: a card in its system's colour that carries the title."""
        system = systems.get(row.system)
        colour = QColor(system.color)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour.darker(125))
        painter.drawRoundedRect(cover, 5, 5)
        painter.setBrush(colour)
        painter.drawRoundedRect(cover.adjusted(0, 0, 0, -26), 5, 5)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        room = cover.adjusted(12, 34, -12, -34)
        painter.drawText(room, Qt.AlignCenter | Qt.TextWordWrap,
                         fit_title(row.title, font, int(room.width()), int(room.height())))
        font.setBold(False)
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 210))
        painter.drawText(QRectF(cover.left() + 6, cover.bottom() - 24, cover.width() - 12, 22),
                         Qt.AlignCenter, QFontMetrics(font).elidedText(system.name, Qt.ElideRight, int(cover.width()) - 12))

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() not in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            return False
        if event.button() != Qt.LeftButton or not self.check_rect(option.rect).contains(event.position().toPoint()):
            return False
        check = index.siblingAtColumn(models.COL_CHECK)
        if event.type() == QEvent.MouseButtonRelease and check.flags() & Qt.ItemIsUserCheckable:
            row = index.data(models.ROW_ROLE)
            model.setData(check, Qt.Unchecked if row.want else Qt.Checked, Qt.CheckStateRole)
        return True                     # a click on the tick box is never a double click on the game


class ListCoverDelegate(QStyledItemDelegate):
    """The Title cell of the list: a small cover, then the title."""
    GAP = 8

    def __init__(self, cache: PosterCache, parent=None):
        super().__init__(parent)
        self.cache = cache

    @staticmethod
    def cover_rect(rect: QRect) -> QRect:
        """The box the small cover is fitted into, at the start of the cell."""
        return QRect(rect.x() + 6, rect.y() + (rect.height() - LIST_H) // 2, LIST_W, LIST_H)

    def paint(self, painter: QPainter, option, index) -> None:
        row = index.data(models.ROW_ROLE)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        style_ = opt.widget.style() if opt.widget else QApplication.style()
        style_.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)      # background and selection
        if row is None:
            return
        box = self.cover_rect(opt.rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setOpacity(1.0 if row.on_device or row.want else 0.5)             # not on the handheld: paler
        pix = self.cache.pixmap("cover", row.system, row.stem)
        if pix is not None:
            size = QSize(pix.size()).scaled(LIST_W, LIST_H, Qt.KeepAspectRatio)
            shown = QRectF(0, 0, size.width(), size.height())
            shown.moveCenter(QRectF(box).center())
            clip = QPainterPath()
            clip.addRoundedRect(shown, 2, 2)
            painter.setClipPath(clip)
            painter.drawPixmap(shown, pix, QRectF(pix.rect()))
            painter.setClipping(False)
            painter.setPen(QPen(QColor(0, 0, 0, 50), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(shown.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(systems.get(row.system).color))
            painter.drawRoundedRect(QRectF(box), 3, 3)
        painter.setOpacity(1.0)
        selected = bool(opt.state & QStyle.State_Selected)
        group = QPalette.Active if opt.state & QStyle.State_Active else QPalette.Inactive
        painter.setPen(opt.palette.color(group, QPalette.HighlightedText if selected else QPalette.Text))
        left = box.right() + self.GAP
        room = opt.rect.right() - left - 4
        painter.setFont(opt.font)
        painter.drawText(QRect(left, opt.rect.y(), max(0, room), opt.rect.height()), Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(opt.font).elidedText(text, Qt.ElideMiddle, room))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + LIST_W + self.GAP + 6, max(hint.height(), LIST_H + 4))


class PosterGrid(QListView):
    def __init__(self, window, cache: PosterCache):
        super().__init__()
        self._window = window
        self.setObjectName("posterGrid")
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(2)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setMouseTracking(True)
        self.setItemDelegate(PosterDelegate(cache, self))
        cache.changed.connect(self.viewport().update)

    def keyPressEvent(self, event) -> None:
        if not games_key(self._window, self, event):
            super().keyPressEvent(event)

    def selectionCommand(self, index, event=None):
        """Select whole rows, as the list does: the two views share one selection."""
        command = super().selectionCommand(index, event)
        return command | QItemSelectionModel.Rows if command != QItemSelectionModel.NoUpdate else command

    def resizeEvent(self, event) -> None:
        """As many tiles as fit side by side, spread over the whole width."""
        width = self.viewport().width()
        columns = max(1, width // TILE_W)
        cell = QSize(width // columns, TILE_H)
        if cell != self.gridSize():
            self.setGridSize(cell)
        super().resizeEvent(event)


class SystemBanner(QFrame):
    """Heads a system's page: its image, its name, and what of it is on the handheld."""

    def __init__(self, cache: PosterCache, art):
        super().__init__()
        self.setObjectName("systemBanner")
        self.cache, self._art = cache, art
        self._system: str | None = None
        self.image = QLabel()
        self.image.setFixedSize(SYSTEM_SIDE, SYSTEM_SIDE)
        self.image.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setObjectName("h1")
        self.detail = QLabel()
        self.detail.setObjectName("bannerDetail")
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addStretch(1)
        text.addWidget(self.title)
        text.addWidget(self.detail)
        text.addStretch(1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(14)
        lay.addWidget(self.image)
        lay.addLayout(text, 1)
        cache.changed.connect(self._picture)
        self.hide()

    def show_system(self, system: str | None, rows: list) -> None:
        """Head the page of this system; None hides the banner."""
        self._system = system
        if system is None:
            self.hide()
            return
        mine = [r for r in rows if r.system == system]
        on = [r for r in mine if r.on_device]
        self.title.setText(systems.get(system).name)
        self.detail.setText(f"{plural(len(on), 'game')} on the handheld  ·  {len(mine)} in all  ·  "
                            f"{human_size(sum(r.size for r in on))} on the handheld")
        self._picture()
        self.show()

    def _picture(self) -> None:
        if self._system is None:
            return
        if self._art().system(self._system) is None:
            self.image.hide()
            return
        pix = self.cache.pixmap("system", self._system)
        if pix is None:
            self.image.clear()          # not read yet: never show another system's image meanwhile
        else:
            shown = pix.scaled(SYSTEM_SIDE * 2, SYSTEM_SIDE * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            shown.setDevicePixelRatio(2)
            self.image.setPixmap(shown)
        self.image.setVisible(True)
