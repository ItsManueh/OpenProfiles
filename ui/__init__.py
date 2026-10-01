"""
Graphical interface built with PySide6 (Qt).

- theme.py           colors, fonts and the Qt style sheet (dark and light)
- widgets.py         reusable widgets (buttons, segmented control, status dot, profile counter)
- icons.py           icon buttons, switch and the window buttons
- line_icons.py      line icons (Lucide) drawn from SVG
- frame.py           frameless windows with the native shadow, corners and behavior
- title_bar.py       title bar of the main window
- dialog.py          base of the dialogs (no Windows frame) and the backdrop behind them
- toast.py           notifications
- sound.py           notification sound
- service.py         BrowserService: Playwright in its own thread, talking through Qt signals
- controller.py      AppController: app state and every action; views only show it
- main_window.py     main window and profile cards
- settings_page.py   settings, shown inside the main window
- profile_dialog.py  create / edit a profile
- confirm_dialog.py  confirmation of destructive actions
- log_window.py      real-time logs window
- release.py         latest release published on GitHub
- single_instance.py only one copy of the app at a time
- app.py             builds the QApplication and starts everything
"""
