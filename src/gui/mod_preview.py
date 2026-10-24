'''Preview of the selected mod: picture and details from Nexus, and for installed mods its files, changelog and
requirements on tabs'''

import html
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.nexusdetails import ModDetails
from src.domain.mod import Mod
from src.globals.constants import translate
from src.gui.skin import FlowLayout
from src.gui.themes import statusColor

# the picture fills the panel's width at 16:9, up to this height
IMAGE_HEIGHT = 230

DETAILS_TAB, FILES_TAB, CHANGELOG_TAB, REQUIREMENTS_TAB = range(4)


@dataclass
class PreviewSections:
    '''What the tabs show for an installed mod; the texts are HTML'''

    # small labels under the title: (text, kind) with kind "", "ok", "muted" or "update"
    chips: list[tuple[str, str]] = field(default_factory=list)
    # linked to Nexus (Open on Nexus and Endorse show), endorsed there, and whether an update is waiting
    linked: bool = False
    endorsed: bool = False
    updateAvailable: bool = False
    facts: list[tuple[str, str]] = field(default_factory=list)
    files: str = ''
    changelog: str = ''
    # the changelog can be fetched from Nexus (the Load Changelog button shows)
    changelogLoadable: bool = False
    requirements: str = ''


def factsHtml(facts: list[tuple[str, str]]) -> str:
    '''Label and value pairs as a two-column table'''
    muted = statusColor('muted').name()
    rows = ''.join(
        f'<tr><td style="color:{muted}; padding:2px 14px 2px 0">{html.escape(label)}</td>'
        f'<td style="padding:2px 0">{html.escape(value)}</td></tr>'
        for label, value in facts
    )
    return f'<table cellspacing="0" cellpadding="0">{rows}</table>' if rows else ''


class ModPreview(QWidget):
    '''The side panel's details: a large picture, the title and chips, buttons for the installed mod, then tabs'''

    changelogWanted = Signal()
    nexusRequested = Signal()
    endorseRequested = Signal()
    updateRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("modPreview")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        self.image = QLabel(content)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(0)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.title = QLabel(content)
        self.title.setObjectName("previewTitle")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight: bold;")
        self.meta = QLabel(content)
        self.meta.setObjectName("previewMeta")
        self.meta.setWordWrap(True)
        self.warning = QLabel(content)
        self.warning.setWordWrap(True)
        self.chips = QWidget(content)
        self.chipsLayout = FlowLayout(self.chips)
        self.actions = QWidget(content)
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 2, 0, 2)
        actions.setSpacing(6)
        self.updateButton = QPushButton(translate("Preview", "Update"), self.actions)
        self.updateButton.setObjectName("previewUpdate")
        self.updateButton.clicked.connect(lambda: self.updateRequested.emit())
        self.nexusButton = QPushButton(translate("Preview", "Open on Nexus"), self.actions)
        self.nexusButton.clicked.connect(lambda: self.nexusRequested.emit())
        self.endorseButton = QPushButton(translate("Preview", "Endorse"), self.actions)
        self.endorseButton.clicked.connect(lambda: self.endorseRequested.emit())
        self.moreButton = QToolButton(self.actions)
        self.moreButton.setObjectName("previewMore")
        self.moreButton.setText("\u22ef")
        self.moreButton.setToolTip(translate("Preview", "More for this mod"))
        self.moreButton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.moreButton.setVisible(False)
        for button in (self.updateButton, self.nexusButton, self.endorseButton, self.moreButton):
            actions.addWidget(button)
        actions.addStretch(1)
        for widget in (self.image, self.title, self.meta, self.chips, self.warning, self.actions):
            layout.addWidget(widget)

        # Details, Files, Changelog, Requirements: only for installed mods
        self.tabs = QTabBar(content)
        self.tabs.setObjectName("previewTabs")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setDocumentMode(True)
        for label in (
            translate("Preview", "Overview"),
            translate("Preview", "Files"),
            translate("Preview", "Changelog"),
            translate("Preview", "Requirements"),
        ):
            self.tabs.addTab(label)
        self.tabs.setVisible(False)
        layout.addWidget(self.tabs)
        self.pages = QStackedWidget(content)
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        layout.addWidget(self.pages)

        details = QWidget(self.pages)
        detailsLayout = QVBoxLayout(details)
        detailsLayout.setContentsMargins(0, 4, 0, 0)
        self.summary = QLabel(details)
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.link = QLabel(details)
        self.link.setOpenExternalLinks(True)
        self.link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.facts = self.richLabel(details)
        for widget in (self.summary, self.link, self.facts):
            detailsLayout.addWidget(widget)
        detailsLayout.addStretch(1)
        self.pages.addWidget(details)

        self.files = self.page(self.richLabel(None))
        changelog = QWidget(self.pages)
        changelogLayout = QVBoxLayout(changelog)
        changelogLayout.setContentsMargins(0, 4, 0, 0)
        self.changelog = self.richLabel(changelog)
        self.loadChangelog = QPushButton(translate("Preview", "Load Changelog"), changelog)
        self.loadChangelog.setToolTip(translate("Preview", "Ask Nexus for this mod's files and their changelogs"))
        self.loadChangelog.clicked.connect(lambda: self.changelogWanted.emit())
        changelogLayout.addWidget(self.changelog)
        changelogLayout.addWidget(self.loadChangelog, 0, Qt.AlignmentFlag.AlignLeft)
        changelogLayout.addStretch(1)
        self.pages.addWidget(changelog)
        self.requirements = self.page(self.richLabel(None))
        # the pages are as tall as the shown one, not the tallest
        self.pages.currentChanged.connect(self.fitPage)

        layout.addStretch(1)
        self.pixmap = QPixmap()
        self.showMod(None, None, '')

    @staticmethod
    def richLabel(parent: QWidget | None) -> QLabel:
        label = QLabel(parent)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        return label

    def page(self, label: QLabel) -> QLabel:
        holder = QWidget(self.pages)
        holderLayout = QVBoxLayout(holder)
        holderLayout.setContentsMargins(0, 4, 0, 0)
        label.setParent(holder)
        holderLayout.addWidget(label)
        holderLayout.addStretch(1)
        self.pages.addWidget(holder)
        return label

    def fitPage(self, index: int) -> None:
        for page in range(self.pages.count()):
            policy = QSizePolicy.Policy.Preferred if page == index else QSizePolicy.Policy.Ignored
            self.pages.widget(page).setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self.pages.adjustSize()

    def showMod(
        self,
        mod: Mod | None,
        details: ModDetails | None,
        image: str,
        remastered: bool = False,
        sections: PreviewSections | None = None,
    ) -> None:
        '''Shows a mod; remastered says whether the game is Remastered, to flag mods not updated for it. Installed
        mods come with sections for the Files, Changelog and Requirements tabs.'''
        self.pixmap = QPixmap(image) if image else QPixmap()
        self.updateImage()
        self.warning.setText('')
        self.showSections(sections)
        if mod is None:
            self.title.setText(translate("Preview", "Select a mod to see its details"))
            self.meta.setText('')
            self.summary.setText('')
            self.link.setText('')
            return
        if details is not None:
            self.showDetails(details, image, remastered, sections)
            return
        self.title.setText(mod.name)
        self.meta.setText('')
        self.summary.setText(
            translate("Preview", "Nexus details haven't been loaded yet (Nexus > Refresh Nexus Details).")
            if mod.nexus and mod.nexus.modId
            else translate("Preview", "Not linked to Nexus (Nexus > Set Nexus Mod...).")
        )
        self.link.setText(self.anchor(mod.nexus.url) if mod.nexus and mod.nexus.modId else '')

    def setMoreMenu(self, menu: QMenu) -> None:
        '''The installed mod's other actions, behind the ... button'''
        self.moreButton.setMenu(menu)
        self.moreButton.setVisible(True)

    def showChips(self, chips: list[tuple[str, str]]) -> None:
        self.chipsLayout.clear()
        for text, kind in chips:
            chip = QLabel(text, self.chips)
            chip.setProperty("chip", kind or True)
            self.chipsLayout.addWidget(chip)
        self.chips.setVisible(bool(chips))
        self.chips.updateGeometry()

    def showSections(self, sections: PreviewSections | None) -> None:
        self.tabs.setVisible(sections is not None)
        self.actions.setVisible(sections is not None)
        self.warning.setVisible(bool(self.warning.text()))
        # an installed, linked mod has the Open on Nexus button instead of the link
        self.link.setVisible(sections is None or not sections.linked)
        self.showChips(sections.chips if sections is not None else [])
        if sections is not None:
            self.updateButton.setVisible(sections.updateAvailable)
            self.nexusButton.setVisible(sections.linked)
            self.endorseButton.setVisible(sections.linked)
            self.endorseButton.setText(
                translate("Preview", "Endorsed") if sections.endorsed else translate("Preview", "Endorse")
            )
            self.endorseButton.setToolTip(
                translate("Preview", "Click to take your endorsement back")
                if sections.endorsed
                else translate("Preview", "Endorse this mod on Nexus")
            )
        if sections is None:
            self.tabs.setCurrentIndex(DETAILS_TAB)
            self.pages.setCurrentIndex(DETAILS_TAB)
            self.facts.setText('')
            self.fitPage(DETAILS_TAB)
            return
        self.facts.setText(factsHtml(sections.facts))
        self.files.setText(sections.files)
        self.changelog.setText(sections.changelog)
        self.loadChangelog.setVisible(sections.changelogLoadable)
        self.requirements.setText(sections.requirements)
        self.fitPage(self.pages.currentIndex())

    def showDetails(
        self, details: ModDetails, image: str, remastered: bool = False, sections: PreviewSections | None = None
    ) -> None:
        '''Shows a mod from Nexus, installed or not'''
        self.pixmap = QPixmap(image) if image else QPixmap()
        self.updateImage()
        self.warning.setText('')
        if sections is None:
            self.showSections(None)
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
        self.warning.setStyleSheet(f"color: {statusColor('warning').name()};")  # follows theme changes
        if remastered and not details.updatedForRemastered:
            self.warning.setText(translate("Preview", "Not updated on Nexus since Remastered came out."))
        self.warning.setVisible(bool(self.warning.text()))

    @staticmethod
    def anchor(url: str) -> str:
        return f'<a href="{html.escape(url)}">' + translate("Preview", "Open on Nexus") + '</a>'

    def updateImage(self) -> None:
        if self.pixmap.isNull():
            self.image.clear()
            self.image.setFixedHeight(0)
            return
        width = max(50, self.scroll.viewport().width() - 28)
        height = min(IMAGE_HEIGHT, round(width * 9 / 16))
        ratio = self.devicePixelRatioF()
        # fill a 16:9 box (cropping the picture's edges if it's another shape) with rounded corners
        scaled = self.pixmap.scaled(
            round(width * ratio),
            round(height * ratio),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        cropped = scaled.copy(
            (scaled.width() - round(width * ratio)) // 2,
            (scaled.height() - round(height * ratio)) // 2,
            round(width * ratio),
            round(height * ratio),
        )
        rounded = QPixmap(cropped.size())
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(rounded.rect()), 10 * ratio, 10 * ratio)
        painter.setClipPath(outline)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
        rounded.setDevicePixelRatio(ratio)
        self.image.setFixedHeight(height)
        self.image.setPixmap(rounded)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.updateImage()
