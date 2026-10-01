"""
The alerts, confirmations and prompts of the web pages. The browsers would not show
them under Playwright, so the app does: in front of the browser window that asked,
with the address of the page, and the answer goes back to the page.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QWidget

from ui import frame
from ui.dialog import Dialog
from ui.widgets import hbox, make_button, make_label

TITLES = {"beforeunload": "Leave site?"}
TEXT_WIDTH = 400


class PageDialog(Dialog):
    """kind: alert, confirm, prompt or beforeunload. After it closes, `answer()` is
    (accepted, text)."""

    def __init__(self, kind: str, message: str, default: str, site: str, parent: QWidget | None = None):
        title = TITLES.get(kind, f"{site or 'This page'} says")
        super().__init__(title, parent=parent)
        self.kind = kind
        if kind == "beforeunload":
            message = "Changes you made may not be saved."
        text = make_label(message, "muted", wrap=True)
        text.setFixedWidth(TEXT_WIDTH)
        text.setMaximumHeight(320)
        self.body.addWidget(text)
        self.input: QLineEdit | None = None
        if kind == "prompt":
            self.input = QLineEdit(default)
            self.input.setFixedWidth(TEXT_WIDTH)
            self.body.addSpacing(12)
            self.body.addWidget(self.input)
        self.body.addSpacing(22)

        accept = make_button("Leave" if kind == "beforeunload" else "OK", "primary", width=104)
        accept.clicked.connect(self.accept)
        accept.setDefault(True)
        if kind == "alert":
            self.body.addLayout(hbox(None, accept))
        else:
            cancel = make_button("Stay" if kind == "beforeunload" else "Cancel", width=104)
            cancel.clicked.connect(self.reject)
            self.body.addLayout(hbox(None, cancel, accept))
        (self.input or accept).setFocus()

    def answer(self) -> tuple[bool, str]:
        accepted = self.result() == Dialog.DialogCode.Accepted or self.kind == "alert"
        return accepted, self.input.text() if self.input is not None and accepted else ""

    def show_in_front(self) -> None:
        """Shows it without blocking the app, in front of the browser window the user is in."""
        self.open()
        frame.bring_to_front(self)
