"""Confirmation dialog for destructive actions."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLayout, QVBoxLayout, QWidget

from ui.widgets import hbox, make_button, make_label


class ConfirmDialog(QDialog):
    """Asks before a destructive action. Enter and Escape cancel: the action must be clicked."""

    def __init__(self, title: str, message: str, action: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 26)
        root.setSpacing(6)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        root.addWidget(make_label(title, name="dialogTitle"))
        text = make_label(message, "muted", wrap=True)
        text.setFixedWidth(380)
        root.addWidget(text)
        root.addSpacing(18)

        cancel = make_button("Cancel", width=110)
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)  # Enter cancels
        confirm = make_button(action, "danger", width=110)
        confirm.clicked.connect(self.accept)
        root.addLayout(hbox(None, cancel, confirm))
        cancel.setFocus()
