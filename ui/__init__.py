"""
Graphical interface built with PySide6 (Qt).

- theme.py           colors, fonts and the Qt style sheet (dark and light)
- widgets.py         reusable widgets (buttons, segmented control, switch, status dot)
- service.py         BrowserService: Playwright in its own thread, talking through Qt signals
- controller.py      AppController: app state and every action; views only show it
- main_window.py     main window and profile cards
- profile_dialog.py  create / edit a profile
- confirm_dialog.py  confirmation of destructive actions
- log_window.py      real-time logs window
- app.py             builds the QApplication and starts everything
"""
