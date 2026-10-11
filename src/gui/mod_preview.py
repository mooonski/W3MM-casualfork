'''Preview of the selected mod: picture and details from Nexus'''

import html
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from src.core.nexusdetails import ModDetails
from src.domain.mod import Mod
from src.globals.constants import translate

IMAGE_HEIGHT = 150


class ModPreview(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("modPreview")
        self.setMinimumWidth(200)
        # the details scroll when the panel is short, instead of being squeezed on top of each other
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        content = QWidget(self.scroll)
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        self.image = QLabel(content)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(0)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.title = QLabel(content)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight: bold;")
        self.meta = QLabel(content)
        self.meta.setWordWrap(True)
        self.warning = QLabel(content)
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #e5a050;")
        self.summary = QLabel(content)
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.link = QLabel(content)
        self.link.setOpenExternalLinks(True)
        self.link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        for widget in (self.image, self.title, self.meta, self.warning, self.summary, self.link):
            layout.addWidget(widget)
        layout.addStretch(1)
        self.pixmap = QPixmap()
        self.showMod(None, None, '')

    def showMod(self, mod: Mod | None, details: ModDetails | None, image: str, remastered: bool = False) -> None:
        '''Shows a mod; remastered says whether the game is Remastered, to flag mods not updated for it'''
        self.pixmap = QPixmap(image) if image else QPixmap()
        self.updateImage()
        self.warning.setText('')
        if mod is None:
            self.title.setText(translate("Preview", "Select a mod to see its details"))
            self.meta.setText('')
            self.summary.setText('')
            self.link.setText('')
            return
        if details is not None:
            self.showDetails(details, image, remastered)
            return
        self.title.setText(mod.name)
        self.meta.setText('')
        self.summary.setText(
            translate("Preview", "Nexus details haven't been loaded yet (Nexus > Refresh Nexus Details).")
            if mod.nexus and mod.nexus.modId
            else translate("Preview", "Not linked to Nexus (Nexus > Set Nexus Mod...).")
        )
        self.link.setText(self.anchor(mod.nexus.url) if mod.nexus and mod.nexus.modId else '')

    def showDetails(self, details: ModDetails, image: str, remastered: bool = False) -> None:
        '''Shows a mod from Nexus, installed or not'''
        self.pixmap = QPixmap(image) if image else QPixmap()
        self.updateImage()
        self.warning.setText('')
        self.title.setText(details.name)
        version = details.version if not details.version or details.version[:1] in 'vV' else 'v' + details.version
        updated = time.strftime('%Y-%m-%d', time.gmtime(details.updated)) if details.updated else ''
        parts = [
            details.category,
            details.author,
            version,
            translate("Preview", "updated") + ' ' + updated if updated else '',
        ]
        self.meta.setText('  ·  '.join(part for part in parts if part))
        self.summary.setText(details.summary)
        self.link.setText(self.anchor(details.url))
        if remastered and not details.updatedForRemastered:
            self.warning.setText(translate("Preview", "Not updated on Nexus since Remastered came out."))

    @staticmethod
    def anchor(url: str) -> str:
        return f'<a href="{html.escape(url)}">' + translate("Preview", "Open on Nexus") + '</a>'

    def updateImage(self) -> None:
        if self.pixmap.isNull():
            self.image.clear()
            self.image.setFixedHeight(0)
            return
        width = max(50, self.scroll.viewport().width() - 8)
        scaled = self.pixmap.scaled(
            width, IMAGE_HEIGHT, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.image.setFixedHeight(scaled.height())
        self.image.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.updateImage()
