'''Parts of the window's skin that a style sheet can't draw: the Play button and the mod list's rows'''

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from src.gui.themes import STEEL, accentColor, isDark, statusColor
from src.gui.tree_widget import GROUP_ROLE

ROW_HEIGHT = 28
# how a row's Update column reads: "update" (an update is available), "error" or "ok"
UPDATE_KIND_ROLE = Qt.ItemDataRole.UserRole + 10
# the number of mods under a separator
GROUP_COUNT_ROLE = Qt.ItemDataRole.UserRole + 11
# on the name: the mod's downloaded Nexus picture, and the line under the name in comfortable rows
PICTURE_ROLE = Qt.ItemDataRole.UserRole + 12
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 13
COMFORTABLE_HEIGHT = 50
PICTURE_SIZE = QSize(64, 36)
# picture tiles for mods without a picture, picked by their name
TILES = ('#23405f', '#3d3460', '#1f4a3f', '#4f3f22', '#512a33', '#2f4426')


class PlayButton(QPushButton):
    '''The accent-coloured button that starts the game, with how it starts (DX12, Steam...) in a small box'''

    PADDING = 16
    ICON = 11

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.mode = ''
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(38)

    def setMode(self, mode: str) -> None:
        self.mode = mode
        self.updateGeometry()
        self.update()

    def boldFont(self) -> QFont:
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.08)
        return font

    def modeFont(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.82)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def modeWidth(self) -> int:
        return QFontMetrics(self.modeFont()).horizontalAdvance(self.mode) + 12 if self.mode else 0

    def sizeHint(self) -> QSize:
        text = QFontMetrics(self.boldFont()).horizontalAdvance(self.text())
        width = self.PADDING + self.ICON + 9 + text + (10 + self.modeWidth() if self.mode else 0) + self.PADDING + 4
        return QSize(width, 38)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = accentColor(self.palette())
        if not self.isEnabled():
            fill = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button)
        elif self.isDown():
            fill = fill.darker(115)
        elif self.underMouse():
            fill = fill.lighter(110)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 8, 8)
        if self.hasFocus():
            painter.setPen(QPen(QColor(255, 255, 255, 140), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
        white = QColor(Qt.GlobalColor.white)
        middle = rect.center().y()
        left = self.PADDING
        triangle = QPainterPath(QPointF(left, middle - self.ICON * 0.6))
        triangle.lineTo(QPointF(left + self.ICON, middle))
        triangle.lineTo(QPointF(left, middle + self.ICON * 0.6))
        triangle.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(white)
        painter.drawPath(triangle)
        left += self.ICON + 9
        painter.setPen(white)
        painter.setFont(self.boldFont())
        textWidth = QFontMetrics(self.boldFont()).horizontalAdvance(self.text())
        painter.drawText(QRectF(left, 0, textWidth + 2, rect.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.mode:
            left += textWidth + 10
            box = QRectF(left, middle - 9, self.modeWidth(), 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 55))
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor(255, 255, 255, 225))
            painter.setFont(self.modeFont())
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.mode)
        painter.end()


class ModListDelegate(QStyledItemDelegate):
    '''Draws the mod list's rows: separators as section headings, the Contents column as small tags, available
    updates as a badge, and an accent edge on the selected rows. Comfortable rows are taller, with the mod's
    picture (or a lettered tile) and a second line under its name.'''

    def __init__(self, parent: QWidget, contentsColumn: int, updateColumn: int, nameColumn: int = 1):
        super().__init__(parent)
        self.contentsColumn = contentsColumn
        self.updateColumn = updateColumn
        self.nameColumn = nameColumn
        self.comfortable = False
        self.pictures: dict[str, QPixmap] = {}

    def setComfortable(self, comfortable: bool) -> None:
        self.comfortable = comfortable

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        height = ROW_HEIGHT
        if self.comfortable and index.data(GROUP_ROLE) is None and not self.isSeparatorRow(index):
            height = COMFORTABLE_HEIGHT
        return QSize(hint.width(), max(hint.height(), height))

    @staticmethod
    def isSeparatorRow(index) -> bool:
        return index.siblingAtColumn(0).data(GROUP_ROLE) is not None

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if index.column() == 0 and index.data(GROUP_ROLE) is not None:
            self.paintSeparator(painter, option, index)
            return
        if self.comfortable and index.column() == self.nameColumn and not self.isSeparatorRow(index):
            self.paintBackground(painter, option, index)
            self.paintNameWithPicture(painter, option, index)
        elif index.column() == self.contentsColumn:
            self.paintBackground(painter, option, index)
            self.paintTags(painter, option, str(index.data() or '').split())
        elif index.column() == self.updateColumn and index.data(UPDATE_KIND_ROLE) == 'update':
            self.paintBackground(painter, option, index)
            self.paintBadge(painter, option, str(index.data() or ''))
        elif self.needsInset(index):
            # the tick sits clear of the selected row's edge (rows under a separator are indented already)
            self.paintBackground(painter, option, index)
            super().paint(painter, self.inset(option), index)
        else:
            super().paint(painter, option, index)
        if index.column() == 0 and option.state & QStyle.StateFlag.State_Selected:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accentColor(option.palette))
            painter.drawRoundedRect(QRectF(0, option.rect.top() + 4, 3, option.rect.height() - 8), 1.5, 1.5)
            painter.restore()

    TICK_INSET = 8

    def inset(self, option: QStyleOptionViewItem) -> QStyleOptionViewItem:
        moved = QStyleOptionViewItem(option)
        moved.rect = option.rect.adjusted(self.TICK_INSET, 0, 0, 0)
        return moved

    @staticmethod
    def needsInset(index) -> bool:
        return index.column() == 0 and index.data(GROUP_ROLE) is None and not index.parent().isValid()

    def editorEvent(self, event, model, option, index) -> bool:
        # clicks on the tick land where paint() drew it
        if self.needsInset(index):
            option = self.inset(option)
        return super().editorEvent(event, model, option, index)

    def paintBackground(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        '''The cell's selection and hover background, without its text'''
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        cell.text = ''
        style = cell.widget.style() if cell.widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, cell, painter, cell.widget)

    @staticmethod
    def smallFont(option: QStyleOptionViewItem, scale: float = 0.85) -> QFont:
        font = QFont(option.font)
        font.setPointSizeF(font.pointSizeF() * scale)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def picture(self, path: str) -> QPixmap | None:
        """A mod's picture cropped to the tile's shape, loaded once"""
        if not path:
            return None
        if path not in self.pictures:
            app = QApplication.instance()
            ratio = app.devicePixelRatio() if isinstance(app, QApplication) else 1.0
            source = QPixmap(path)
            if source.isNull():
                self.pictures[path] = QPixmap()
            else:
                size = PICTURE_SIZE * ratio
                scaled = source.scaled(
                    size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
                )
                left = (scaled.width() - size.width()) // 2
                top = (scaled.height() - size.height()) // 2
                cropped = scaled.copy(left, top, size.width(), size.height())
                cropped.setDevicePixelRatio(ratio)
                self.pictures[path] = cropped
        picture = self.pictures[path]
        return None if picture.isNull() else picture

    @staticmethod
    def initials(name: str) -> str:
        words = [word for word in name.replace('[', ' ').replace(']', ' ').split() if word[:1].isalnum()]
        if words and words[0].casefold() == 'mod.io':
            words = words[1:]
        return ''.join(word[0] for word in words[:2]).upper()

    def paintNameWithPicture(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        rect = QRectF(option.rect).adjusted(4, 0, -4, 0)
        tile = QRectF(0, 0, PICTURE_SIZE.width(), PICTURE_SIZE.height())
        tile.moveTopLeft(QPointF(rect.left(), rect.center().y() - PICTURE_SIZE.height() / 2))
        name = str(index.data() or '')
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outline = QPainterPath()
        outline.addRoundedRect(tile, 5, 5)
        picture = self.picture(str(index.data(PICTURE_ROLE) or ''))
        if picture is not None:
            painter.setClipPath(outline)
            painter.drawPixmap(tile.toRect(), picture)
            painter.setClipping(False)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(TILES[sum(map(ord, name)) % len(TILES)]))
            painter.drawPath(outline)
            font = self.smallFont(option, 0.95)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor('#d7e2ee'))
            painter.drawText(tile, Qt.AlignmentFlag.AlignCenter, self.initials(name))
        left = tile.right() + 10
        width = max(0.0, rect.right() - left)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        foreground = cell.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if brush is not None and not selected:
            foreground = brush.color()
        font = QFont(cell.font)
        metrics = QFontMetrics(font)
        subtitle = str(index.data(SUBTITLE_ROLE) or '')
        small = self.smallFont(option, 0.88)
        small.setWeight(QFont.Weight.Normal)
        smallMetrics = QFontMetrics(small)
        total = metrics.height() + (smallMetrics.height() + 1 if subtitle else 0)
        top = rect.top() + (rect.height() - total) / 2
        painter.setFont(font)
        painter.setPen(foreground)
        painter.drawText(
            QRectF(left, top, width, metrics.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(name, Qt.TextElideMode.ElideRight, int(width)),
        )
        if subtitle:
            painter.setFont(small)
            painter.setPen(statusColor('muted', option.palette))
            painter.drawText(
                QRectF(left, top + metrics.height() + 1, width, smallMetrics.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                smallMetrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, int(width)),
            )
        painter.restore()

    def paintTags(self, painter: QPainter, option: QStyleOptionViewItem, tags: list[str]) -> None:
        if not tags:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.smallFont(option)
        metrics = QFontMetrics(font)
        painter.setFont(font)
        border = option.palette.color(QPalette.ColorRole.Light if isDark(option.palette) else QPalette.ColorRole.Mid)
        text = statusColor('muted', option.palette)
        height = metrics.height() + 2
        left = option.rect.left() + 6
        top = option.rect.top() + (option.rect.height() - height) / 2
        painter.setClipRect(option.rect)
        for tag in tags:
            box = QRectF(left, top, metrics.horizontalAdvance(tag) + 10, height)
            painter.setPen(QPen(border, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            painter.setPen(text)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, tag)
            left = box.right() + 4
        painter.restore()

    def paintBadge(self, painter: QPainter, option: QStyleOptionViewItem, text: str) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.smallFont(option, 0.9)
        font.setBold(True)
        metrics = QFontMetrics(font)
        painter.setFont(font)
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, option.rect.width() - 18)
        width = metrics.horizontalAdvance(text) + 14
        box = QRectF(0, 0, width, metrics.height() + 4)
        box.moveCenter(QRectF(option.rect).center())
        color = statusColor('update', option.palette)
        fill = (
            QColor(STEEL['blueSoft'])
            if isDark(option.palette)
            else QColor(color.red(), color.green(), color.blue(), 28)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(color)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def paintSeparator(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        '''A separator heading: its name in small capitals, how many mods it has, and a rule to the edge'''
        self.paintBackground(painter, option, index)
        count = index.data(GROUP_COUNT_ROLE)
        label = str(index.data() or '')
        suffix = f"  ({count})"
        if count is not None and label.endswith(suffix):
            label = label[: -len(suffix)]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.smallFont(option, 0.88)
        font.setBold(True)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        metrics = QFontMetrics(font)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        muted = statusColor('muted', option.palette)
        rect = QRectF(option.rect)
        left = rect.left() + 4
        painter.setFont(font)
        painter.setPen(option.palette.color(QPalette.ColorRole.HighlightedText) if selected else muted)
        painter.drawText(QRectF(left, rect.top(), rect.width(), rect.height()), Qt.AlignmentFlag.AlignVCenter, label)
        left += metrics.horizontalAdvance(label) + 8
        if count is not None:
            plain = self.smallFont(option, 0.88)
            plain.setWeight(QFont.Weight.Normal)
            painter.setFont(plain)
            number = str(count)
            painter.setPen(muted)
            painter.drawText(
                QRectF(left, rect.top(), rect.width(), rect.height()), Qt.AlignmentFlag.AlignVCenter, number
            )
            left += QFontMetrics(plain).horizontalAdvance(number) + 10
        if left < rect.right() - 12:
            line = option.palette.color(QPalette.ColorRole.Mid)
            painter.setPen(QPen(line, 1))
            middle = round(rect.center().y()) + 0.5
            painter.drawLine(QPointF(left, middle), QPointF(rect.right() - 12, middle))
        painter.restore()
