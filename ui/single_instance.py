"""
Only one copy of the app per data folder: two copies would edit the same
profiles.json at once and could open the same profile twice. Opening the app
again brings the running window to the front and the new copy closes.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("app.gui")

CONNECT_TIMEOUT_MS = 500


def server_name(data_dir: Path) -> str:
    """Name of the local server; one per data folder (source code, .exe, tests...)."""
    digest = hashlib.sha1(str(data_dir.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    return f"OpenProfiles-{digest}"


class SingleInstance(QObject):
    activation_requested = Signal()  # another copy was opened: show this window

    def __init__(self, data_dir: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.name = server_name(data_dir)
        self._server: QLocalServer | None = None

    def notify_running_instance(self) -> bool:
        """If another copy is running, asks it to come to the front and returns True."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        if sys.platform == "win32":
            # Lets the running copy take the foreground (Windows only allows it to the active process).
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        socket.write(b"show")
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    def listen(self) -> None:
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.name):
            QLocalServer.removeServer(self.name)  # left behind by a copy that crashed
            if not server.listen(self.name):
                log.warning("Could not guard against a second copy of the app: %s", server.errorString())
                return
        server.newConnection.connect(self._on_connection)
        self._server = server

    def _on_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            connection.close()
        log.info("The app was opened again; showing this window")
        self.activation_requested.emit()
