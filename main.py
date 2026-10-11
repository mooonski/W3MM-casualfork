'''Witcher 3 Mod Manager main module'''

import sys
import tempfile
import traceback
from argparse import ArgumentParser
from os import environ, path


def logStartupFailure(details: str) -> str | None:
    log_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="TW3MM-startup-", suffix=".log", delete=False
        ) as log:
            log.write(details)
            log_path = log.name
    except OSError as error:
        details += f"\nCould not write startup log: {error}\n"
    if log_path:
        details += f"\nStartup log: {log_path}\n"
    if sys.stderr is not None:
        try:
            sys.stderr.write(details)
        except (OSError, ValueError):
            pass
    return log_path


if __name__ == "__main__":
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from src.configuration.config import Configuration
        from src.core.model import Model
        from src.globals import data
        from src.gui.alerts import *
        from src.gui.main_widget import CustomMainWidget
        from src.gui.main_window import CustomMainWindow
        from src.gui.themes import ManagerStyle, get_dark_palette, get_light_palette, get_system_palette
        from src.util.util import *

        # correct screen scaling
        if "QT_DEVICE_PIXEL_RATIO" in environ:
            del environ["QT_DEVICE_PIXEL_RATIO"]
        environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

        # log and show uncaught exceptions
        def _logUncaughtException(exc_type, exc_value, exc_traceback):
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=sys.stderr)
            msg = QMessageBox(None)
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setWindowTitle("Unexpected Error")
            msg.setText(str(exc_value))
            msg.setDetailedText(tb_text)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

        sys.excepthook = _logUncaughtException

        documentsPath: str = ''
        gamePath: str = ''
        configPath: str = ''
        nxmLink: str = ''
        try:
            parser = ArgumentParser(description=getVersionString())
            parser.add_argument(
                "-d",
                "--debug",
                dest="debug",
                action="store_true",
                default=False,
                help="show debug information on errors",
            )
            parser.add_argument(
                "-v",
                "--version",
                dest="version",
                action="store_true",
                default=False,
                help="show version information and exit",
            )
            dirs = parser.add_argument_group(title="start overrides")
            dirs.add_argument(
                "-u", "--userdocuments", dest="userdocuments", type=str, default="", help="override the documents path"
            )
            dirs.add_argument("-g", "--game", dest="game", type=str, default="", help="override the game path")
            dirs.add_argument("-c", "--config", dest="config", type=str, default="", help="override the config path")
            parser.add_argument("--nxm", dest="nxm", type=str, default="", help="open a nxm:// link from Nexus Mods")
            args = parser.parse_args()
            data.debug = args.debug
            if args.version:
                print(getVersionString())
                sys.exit()
            documentsPath = args.userdocuments
            gamePath = args.game
            configPath = args.config
            nxmLink = args.nxm
        except Exception as e:
            print(str(e))

        data.app = QApplication(sys.argv)
        data.config = Configuration(documentsPath, gamePath, configPath)
        # a nxm link handler started by Windows uses the same overrides as the instance that registered it
        for flag, value in (("-g", gamePath), ("-u", documentsPath), ("-c", configPath)):
            if value:
                data.config.startarguments += [flag, path.abspath(value)]

        if nxmLink:
            from src.core.nxm import forward, instanceName, parseNxm
            from src.gui.nxm_server import sendToRunningInstance

            link = parseNxm(nxmLink)
            if link is not None and link.game != "witcher3":
                previous = data.config.previousnxm
                if not previous or not forward(previous, nxmLink):
                    QMessageBox.warning(
                        None, "Nexus link", f"No other program is set up to open {link.game} links:\n{nxmLink}"
                    )
                sys.exit(0)
            if sendToRunningInstance(instanceName(data.config.configuration), nxmLink):
                sys.exit(0)

        data.app.setStyle(ManagerStyle())

        data.dark_palette = get_dark_palette()
        data.light_palette = get_light_palette()

        if data.config.theme == 'Dark':
            data.app.setPalette(data.dark_palette)
        elif data.config.theme == 'Light':
            data.app.setPalette(data.light_palette)
        else:  # Follow System
            data.app.setPalette(get_system_palette())

        translateToChosenLanguage()

        if not Configuration.getCorrectGamePath(data.config.gameexe):
            if not reconfigureGamePath():
                sys.exit(1)

        try:
            modModel = Model()
        except IOError as err:
            print(err, file=sys.stderr)
            if MessageAlertOtherInstance() == QMessageBox.StandardButton.Yes:
                modModel = Model(ignorelock=True)
            else:
                sys.exit(1)
        except Exception as e:
            MessageInitializationFailed(formatUserError(e))
            sys.exit(1)

        mainWindow = CustomMainWindow()
        mainWidget = CustomMainWidget(mainWindow, modModel)
        mainWidget.checkTheme()
        mainWindow.dropCallback = mainWidget.installModFiles
        data.app.setWindowIcon(getIcon("w3a.ico"))

        from PySide6.QtCore import QTimer

        from src.core.nxm import instanceName
        from src.gui.nxm_server import NxmServer

        nxmServer = NxmServer(instanceName(data.config.configuration), mainWidget)
        nxmServer.received.connect(mainWidget.handleNxm)

        mainWindow.show()
        if nxmLink:
            QTimer.singleShot(0, lambda: mainWidget.handleNxm(nxmLink))

        ret = data.app.exec()
        data.config.saveWindowSettings(mainWidget, mainWindow)
        data.config.write_priority().join()
        data.config.write_config().join()
        modModel.write()

        sys.exit(ret)

    except Exception as e:
        tb_text = traceback.format_exc()
        startup_log = logStartupFailure(tb_text)
        if startup_log:
            tb_text += f"\nStartup log: {startup_log}"
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            _app = QApplication.instance() or QApplication(sys.argv)
            msg = QMessageBox(None)
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setWindowTitle("Unexpected Error")
            msg.setText(str(e))
            msg.setDetailedText(tb_text)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
        except Exception:
            pass
        sys.exit(1)
