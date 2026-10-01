"""
Line icons drawn from SVG: a few icons of Lucide (lucide.dev), the set used by
shadcn/ui, so they match the rest of the interface. They are rendered with QtSvg
in any color and size, so they stay sharp on every screen.

Lucide is distributed under the ISC license:
    Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part
    of Feather (MIT). All other copyright (c) for Lucide are held by Lucide
    Contributors 2022.

    Permission to use, copy, modify, and/or distribute this software for any
    purpose with or without fee is hereby granted, provided that the above
    copyright notice and this permission notice appear in all copies.

    THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
    WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
    MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
    SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
    WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION
    OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
    CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""

from __future__ import annotations

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# The inner elements of each 24x24 icon; they are drawn with lines, without fill.
ICONS = {
    "pencil": (
        '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 '
        '.623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
        '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>'
    ),
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73'
        "l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38"
        "a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18"
        "a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08"
        "a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08"
        'a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'
    ),
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "circle-check": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "circle-alert": '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    "triangle-alert": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "circle-play": '<circle cx="12" cy="12" r="10"/><path d="M10 8l6 4-6 4z"/>',
    "power": '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
    "download": ('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>'),
    "folder": (
        '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4'
        'a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'
    ),
    "copy": (
        '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
        '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
    ),
    "save": (
        '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5'
        'a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>'
    ),
    "eraser": (
        '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/>'
        '<path d="M22 21H7"/><path d="m5 11 9 9"/>'
    ),
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
    ),
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "sliders": (
        '<path d="M21 4h-7"/><path d="M10 4H3"/><path d="M21 12h-9"/><path d="M8 12H3"/><path d="M21 20h-5"/>'
        '<path d="M12 20H3"/><path d="M14 2v4"/><path d="M8 10v4"/><path d="M16 18v4"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
        '<path d="M2 12h20"/>'
    ),
    "archive": (
        '<rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/>'
        '<path d="M10 12h4"/>'
    ),
    # Pieces of icons that move on their own (see AnimatedIconButton in icons.py).
    "trash-lid": '<path d="M3 6h18"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "trash-can": '<path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "copy-front": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>',
    "copy-back": '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "grip": (
        '<circle cx="9" cy="12" r="1"/><circle cx="9" cy="5" r="1"/><circle cx="9" cy="19" r="1"/>'
        '<circle cx="15" cy="12" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="19" r="1"/>'
    ),
}
STROKE = 2.0  # line width in the 24x24 grid of the icons
PIXMAP_RATIO = 3  # pixmaps are drawn larger so they stay sharp on high-DPI screens


@cache
def _renderer(name: str, color: str, opacity: float, stroke: float) -> QSvgRenderer:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        f'<g fill="none" stroke="{color}" stroke-opacity="{opacity}" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g></svg>'
    )
    return QSvgRenderer(QByteArray(svg.encode("utf-8")))


def paint(
    painter: QPainter, name: str, rect: QRectF, color: QColor, *, stroke: float = STROKE, angle: float = 0.0
) -> None:
    """Draws the icon `name` filling `rect`, optionally rotated around its center."""
    renderer = _renderer(name, color.name(), round(color.alphaF(), 3), stroke)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if angle:
        center = rect.center()
        painter.translate(center)
        painter.rotate(angle)
        painter.translate(-center)
    renderer.render(painter, rect)
    painter.restore()


def pixmap(name: str, color: QColor, size: int, *, stroke: float = STROKE) -> QPixmap:
    """The icon as a pixmap of `size` logical pixels, e.g. for QIcon or style sheets."""
    result = QPixmap(size * PIXMAP_RATIO, size * PIXMAP_RATIO)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    paint(painter, name, QRectF(result.rect()), color, stroke=stroke)
    painter.end()
    result.setDevicePixelRatio(PIXMAP_RATIO)
    return result
