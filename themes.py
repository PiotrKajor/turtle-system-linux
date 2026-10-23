"""Colour themes for the whole interface (the canvas keeps the program's own colours).

Each theme sets the Qt palette for all widgets plus the editor, syntax, header and ruler
colours, so text always contrasts with its background. "System" follows the desktop's
light or dark palette and picks matching editor colours.
"""

from PySide6.QtGui import QColor, QPalette

_LIGHT_SYNTAX = {"keyword": "#b00000", "comment": "#1a3cff", "string": "#008000",
                 "number": "#a05000", "query": "#8000a0"}
_DARK_SYNTAX = {"keyword": "#ff7b72", "comment": "#79b8ff", "string": "#7ee787",
                "number": "#ffa657", "query": "#d2a8ff"}

THEMES = {
    "classic": {
        "name": "Classic (like the original)", "dark": False,
        "window": "#f0f0f0", "base": "#ffffff", "alt_base": "#f5f5f5", "text": "#000000",
        "button": "#e8e8e8", "button_text": "#000000", "highlight": "#3874d8",
        "highlight_text": "#ffffff", "disabled": "#8a8a8a", "link": "#1a3cff",
        "editor_bg": "#ffffff", "editor_fg": "#000000", "current_line": "#eef3ff",
        "gutter_bg": "#ececec", "gutter_fg": "#404040", "error_line": "#ffd0d0",
        "error_text": "#c00000", "lang_bg": "#008080", "lang_fg": "#ffffff",
        "title_bg": "#00e5ff", "title_fg": "#000000", "ruler": "#1a3cff",
        "canvas_border": "#808080", **_LIGHT_SYNTAX},
    "dark": {
        "name": "Dark", "dark": True,
        "window": "#2b2d31", "base": "#1e1f22", "alt_base": "#26282c", "text": "#e6e6e6",
        "button": "#3a3d42", "button_text": "#e6e6e6", "highlight": "#3d6fb4",
        "highlight_text": "#ffffff", "disabled": "#7a7a7a", "link": "#79b8ff",
        "editor_bg": "#1e1f22", "editor_fg": "#e6e6e6", "current_line": "#2c3039",
        "gutter_bg": "#26282c", "gutter_fg": "#8b8f96", "error_line": "#5a2328",
        "error_text": "#ff6b6b", "lang_bg": "#0f6e6e", "lang_fg": "#ffffff",
        "title_bg": "#164a5c", "title_fg": "#e6f8ff", "ruler": "#79b8ff",
        "canvas_border": "#6a6a6a", **_DARK_SYNTAX},
    "contrast": {
        "name": "High contrast", "dark": True,
        "window": "#000000", "base": "#000000", "alt_base": "#101010", "text": "#ffffff",
        "button": "#1a1a1a", "button_text": "#ffffff", "highlight": "#ffff00",
        "highlight_text": "#000000", "disabled": "#b0b0b0", "link": "#00ffff",
        "editor_bg": "#000000", "editor_fg": "#ffffff", "current_line": "#202040",
        "gutter_bg": "#101010", "gutter_fg": "#ffff00", "error_line": "#800000",
        "error_text": "#ff4040", "lang_bg": "#ffff00", "lang_fg": "#000000",
        "title_bg": "#00ffff", "title_fg": "#000000", "ruler": "#ffff00",
        "canvas_border": "#ffffff",
        "keyword": "#ffff00", "comment": "#00ffff", "string": "#00ff00", "number": "#ff9f40",
        "query": "#ff80ff"},
    "solarized_light": {
        "name": "Solarized Light", "dark": False,
        "window": "#eee8d5", "base": "#fdf6e3", "alt_base": "#f5efdc", "text": "#073642",
        "button": "#e4ddc8", "button_text": "#073642", "highlight": "#1a6aa6",
        "highlight_text": "#fdf6e3", "disabled": "#687878", "link": "#1a6aa6",
        "editor_bg": "#fdf6e3", "editor_fg": "#073642", "current_line": "#f2ead3",
        "gutter_bg": "#eee8d5", "gutter_fg": "#4f6168", "error_line": "#f5c7c0",
        "error_text": "#b8221f", "lang_bg": "#1b7a73", "lang_fg": "#fdf6e3",
        "title_bg": "#93d4ce", "title_fg": "#073642", "ruler": "#268bd2",
        "canvas_border": "#93a1a1",
        "keyword": "#a83a0e", "comment": "#268bd2", "string": "#5c6b00", "number": "#7d5e00",
        "query": "#4f53a8"},
    "solarized_dark": {
        "name": "Solarized Dark", "dark": True,
        "window": "#073642", "base": "#002b36", "alt_base": "#03313d", "text": "#eee8d5",
        "button": "#0b4251", "button_text": "#eee8d5", "highlight": "#1b6aa3",
        "highlight_text": "#fdf6e3", "disabled": "#7c939a", "link": "#2aa198",
        "editor_bg": "#002b36", "editor_fg": "#eee8d5", "current_line": "#073642",
        "gutter_bg": "#073642", "gutter_fg": "#93a1a1", "error_line": "#4a1f22",
        "error_text": "#ff6e64", "lang_bg": "#2aa198", "lang_fg": "#002b36",
        "title_bg": "#0f5a6a", "title_fg": "#eee8d5", "ruler": "#2aa198",
        "canvas_border": "#586e75",
        "keyword": "#ff8c5a", "comment": "#5fafef", "string": "#b5c94a", "number": "#e0b84a",
        "query": "#a3a7f0"},
    "sepia": {
        "name": "Sepia (easy on the eyes)", "dark": False,
        "window": "#e9dfc9", "base": "#f6eedb", "alt_base": "#efe5cf", "text": "#3b2e1e",
        "button": "#e0d4b8", "button_text": "#3b2e1e", "highlight": "#8b5a2b",
        "highlight_text": "#fff8e8", "disabled": "#7d6e55", "link": "#7a3e10",
        "editor_bg": "#f6eedb", "editor_fg": "#3b2e1e", "current_line": "#ece0c4",
        "gutter_bg": "#e9dfc9", "gutter_fg": "#66573f", "error_line": "#efc4b4",
        "error_text": "#a01e10", "lang_bg": "#6b4a2b", "lang_fg": "#fff8e8",
        "title_bg": "#d8c39a", "title_fg": "#3b2e1e", "ruler": "#7a3e10",
        "canvas_border": "#9a8a70",
        "keyword": "#9b1c1c", "comment": "#2f5d8a", "string": "#3d6b21", "number": "#8a5a00",
        "query": "#6b2f7a"},
    "green_terminal": {
        "name": "Green terminal", "dark": True, "ui_font": "Liberation Mono",
        "window": "#020d05", "base": "#010a04", "alt_base": "#03140a", "text": "#4dff7a",
        "button": "#062a12", "button_text": "#6aff8e", "highlight": "#14702d",
        "highlight_text": "#d6ffe0", "disabled": "#1f6b33", "link": "#b8ffc8",
        "editor_bg": "#010a04", "editor_fg": "#4dff7a", "current_line": "#062a12",
        "gutter_bg": "#03140a", "gutter_fg": "#1f9a40", "error_line": "#3a1a00",
        "error_text": "#ffb000", "lang_bg": "#33ff66", "lang_fg": "#010a04",
        "title_bg": "#0b4a1d", "title_fg": "#c8ffd6", "ruler": "#33ff66",
        "canvas_border": "#33ff66",
        "keyword": "#c8ffd6", "comment": "#1f9a40", "string": "#9bbc0f", "number": "#ffb000",
        "query": "#6affd8"},
}

ORDER = ["system", "classic", "dark", "contrast", "solarized_light", "solarized_dark", "sepia",
         "green_terminal"]
NAMES = {"system": "System (follow desktop)", **{k: v["name"] for k, v in THEMES.items()}}


def resolve(key, system_palette):
    """The colour set for theme `key`; 'system' adapts to the desktop's light/dark palette."""
    if key in THEMES:
        return THEMES[key]
    pal = system_palette
    dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
    base = THEMES["dark" if dark else "classic"]
    hex_ = lambda role: pal.color(role).name()  # noqa: E731
    return {**base, "system": True,
            "editor_bg": hex_(QPalette.ColorRole.Base), "editor_fg": hex_(QPalette.ColorRole.Text),
            "gutter_bg": hex_(QPalette.ColorRole.AlternateBase),
            "gutter_fg": hex_(QPalette.ColorRole.PlaceholderText),
            "base": hex_(QPalette.ColorRole.Base), "text": hex_(QPalette.ColorRole.Text)}


def palette(t):
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t["window"], QPalette.ColorRole.WindowText: t["text"],
        QPalette.ColorRole.Base: t["base"], QPalette.ColorRole.AlternateBase: t["alt_base"],
        QPalette.ColorRole.Text: t["text"], QPalette.ColorRole.Button: t["button"],
        QPalette.ColorRole.ButtonText: t["button_text"],
        QPalette.ColorRole.Highlight: t["highlight"],
        QPalette.ColorRole.HighlightedText: t["highlight_text"],
        QPalette.ColorRole.ToolTipBase: t["base"], QPalette.ColorRole.ToolTipText: t["text"],
        QPalette.ColorRole.PlaceholderText: t["disabled"], QPalette.ColorRole.Link: t["link"],
        QPalette.ColorRole.BrightText: t["error_text"],
    }
    for role, colour in roles.items():
        p.setColor(role, QColor(colour))
    # 3D shades (tab edges, frames) derived from the button colour
    button = QColor(t["button"])
    dark = t.get("dark", False)
    p.setColor(QPalette.ColorRole.Light, button.lighter(160 if dark else 125))
    p.setColor(QPalette.ColorRole.Midlight, button.lighter(130 if dark else 110))
    p.setColor(QPalette.ColorRole.Mid, button.darker(130))
    p.setColor(QPalette.ColorRole.Dark, button.darker(160))
    p.setColor(QPalette.ColorRole.Shadow, QColor("#000000") if not dark else button.darker(250))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["disabled"]))
    return p


def contrasting_text(rgb):
    """Black or white text for a background colour given as 0xRRGGBB."""
    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 140 else "#ffffff"
