'''Parts of the window's skin that a style sheet can't draw: the Play button and the mod list's rows'''

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from src.gui.themes import STEEL, accentColor, isDark, statusColor
from src.gui.tree_widget import GROUP_ROLE

ROW_HEIGHT = 28
# how a row's Update column reads: "update" (an update is available), "error" or "ok"
UPDATE_KIND_ROLE = Qt.ItemDataRole.UserRole + 10
# the number of mods under a separator
GROUP_COUNT_ROLE = Qt.ItemDataRole.UserRole + 11


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
    updates as a badge, and an accent edge on the selected rows'''

    def __init__(self, parent: QWidget, contentsColumn: int, updateColumn: int):
        super().__init__(parent)
        self.contentsColumn = contentsColumn
        self.updateColumn = updateColumn

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        return QSize(hint.width(), max(hint.height(), ROW_HEIGHT))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if index.column() == 0 and index.data(GROUP_ROLE) is not None:
            self.paintSeparator(painter, option, index)
            return
        if index.column() == self.contentsColumn:
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
