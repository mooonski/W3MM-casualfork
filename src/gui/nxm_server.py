'''Passing nxm:// links to an already running instance'''

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class NxmServer(QObject):
    '''Listens for links sent by later starts of the program'''

    received = Signal(str)

    def __init__(self, name: str, parent: QObject | None = None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self.accept)
        self.listening = self.server.listen(name)
        if not self.listening:
            # a stale socket file is left behind after a crash on Linux and macOS
            QLocalServer.removeServer(name)
            self.listening = self.server.listen(name)
        if not self.listening:
            print('could not listen for nxm links:', self.server.errorString())

    def accept(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            if socket.bytesAvailable() == 0:
                socket.waitForReadyRead(2000)
            text = bytes(socket.readAll().data()).decode('utf-8', errors='replace')
            socket.disconnectFromServer()
            socket.deleteLater()
            for line in text.splitlines():
                if line.strip():
                    self.received.emit(line.strip())


def sendToRunningInstance(name: str, url: str, timeout: int = 2000) -> bool:
    '''Hands url to the instance listening on name. Returns False if none is running.'''
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout):
        return False
    message = (url + '\n').encode('utf-8')
    if socket.write(message) != len(message):
        return False
    socket.flush()
    # waitForBytesWritten reports False when flush() already wrote everything, so only wait for what's left
    sent = socket.bytesToWrite() == 0 or socket.waitForBytesWritten(timeout)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.waitForDisconnected(timeout)
    return sent
