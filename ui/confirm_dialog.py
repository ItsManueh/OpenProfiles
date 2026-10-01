"""Confirmation dialog for destructive actions."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ui.dialog import Dialog
from ui.widgets import hbox, make_button, make_label


class ConfirmDialog(Dialog):
    """Asks before an action (red when it is destructive). Enter and Escape cancel: the action
    must be clicked."""

    def __init__(
        self, title: str, message: str, action: str, parent: QWidget | None = None, *, variant: str = "danger"
    ):
        super().__init__(title, parent=parent)
        text = make_label(message, "muted", wrap=True)
        text.setFixedWidth(380)
        self.body.addWidget(text)
        self.body.addSpacing(22)

        cancel = make_button("Cancel", width=104)
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)  # Enter cancels
        confirm = make_button(action, variant, width=104)
        confirm.clicked.connect(self.accept)
        self.body.addLayout(hbox(None, cancel, confirm))
        cancel.setFocus()
