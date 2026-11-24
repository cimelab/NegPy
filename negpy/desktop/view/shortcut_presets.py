"""Shortcut sets a user can switch to in one go: NegPy's own, and one laid out like Lightroom
Classic's.

A preset is a set of overrides on top of the registry defaults, exactly what a user's own
Customize edits are, so it saves, loads and clashes the same way (``merge_bindings``): a NegPy
default whose key a preset hands to another action is left unbound. ``""`` unbinds an action
on purpose.

`Ctrl` is ⌘ on macOS, as everywhere in the registry, so ``Ctrl+Shift+E`` is Lightroom's ⇧⌘E.
"""

from __future__ import annotations

from negpy.desktop.view.shortcut_registry import default_bindings, merge_bindings
from negpy.kernel.system.i18n import tr

LIGHTROOM: dict[str, str] = {
    # Modules and views: G Grid, E Loupe, D Develop, Z zoom, Tab and Shift+Tab panels, F6 Filmstrip.
    "toggle_light_table": "G",
    "show_loupe": "E",
    "show_develop": "D",
    "toggle_zoom": "Z",
    "toggle_right_panel": "Tab",
    "toggle_side_panels": "Shift+Tab",
    "toggle_film_strip": "F6",
    "toggle_compare": "\\",
    "cycle_frame_info": "I",
    "toggle_clipping_overlay": "J",
    # Library: import, show in Finder/Explorer, flag as pick, reject.
    "import_roll": "Ctrl+Shift+I",
    "show_in_folder": "Ctrl+R",
    "toggle_keep": "P",
    "toggle_reject": "X",
    # Develop tools: R Crop, Q Spot Removal, W White Balance, K Brush, M Linear and
    # Shift+M Radial Gradient, [ and ] brush size, ⌘[ and ⌘] rotate.
    "manual_crop": "R",
    "pick_dust": "Q",
    "pick_wb": "W",
    "local_draw": "K",
    "local_gradient": "M",
    "local_oval": "Shift+M",
    "manual_size_dec": "[",
    "manual_size_inc": "]",
    "rotate_ccw": "Ctrl+[",
    "rotate_cw": "Ctrl+]",
    "toggle_auto_both": "Ctrl+U",
    # Settings: ⇧⌘C copy, ⇧⌘V paste, ⇧⌘Z redo.
    "copy": "Ctrl+Shift+C",
    "copy_with_bounds": "Ctrl+Alt+C",
    "paste": "Ctrl+Shift+V",
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Shift+Z",
    # Export: ⇧⌘E opens the export settings, ⌥⇧⌘E exports with the previous (current) settings.
    "export_preferences": "Ctrl+Shift+E",
    "export": "Ctrl+Alt+Shift+E",
    "open_preferences": "Ctrl+,",
    "toggle_simple_mode": "",
    # NegPy's letter grid for the printing sliders (Q/A density, W/S grade, E/D magenta,
    # R/F yellow, T/G temperature) sits on Lightroom's tool keys. Unbound rather than left
    # half-working; every one can be bound again in Customize Shortcuts.
    "density_up": "",
    "density_down": "",
    "grade_up": "",
    "grade_down": "",
    "magenta_up": "",
    "magenta_down": "",
    "yellow_up": "",
    "yellow_down": "",
    "temp_warm": "",
    "temp_cool": "",
}

# key -> (menu label, overrides)
PRESETS: dict[str, tuple[str, dict[str, str]]] = {
    "negpy": (tr("NegPy"), {}),
    "lightroom": (tr("Lightroom"), LIGHTROOM),
}


def preset_bindings(name: str) -> dict[str, str]:
    """Every action's key under preset *name*."""
    if name not in PRESETS:
        raise KeyError(f"Unknown shortcut preset {name!r}")
    overrides = PRESETS[name][1]
    return merge_bindings(overrides) if overrides else default_bindings()


def matching_preset(bindings: dict[str, str]) -> str:
    """The preset *bindings* are exactly, or "" for a set of the user's own."""
    for name in PRESETS:
        if preset_bindings(name) == bindings:
            return name
    return ""


# The SmartConvert Control Panel (Filmomat) sends fixed keyboard keys. Its layer sits on top of
# whichever set is in use: each panel key runs a NegPy action, and any action of that set on the
# same key goes unbound while the layer is on. Ctrl is ⌘ and Alt is ⌥ on macOS.
SMARTCONVERT_PANEL: dict[str, str] = {
    "Q": "cyan_inc",
    "A": "cyan_dec",
    "W": "magenta_up",
    "S": "magenta_down",
    "E": "yellow_up",
    "D": "yellow_down",
    "Up": "density_up",
    "Down": "density_down",
    "Alt+Up": "grade_up",
    "Alt+Down": "grade_down",
    # The panel's Saturation keys.
    "Ctrl+Up": "temp_warm",
    "Ctrl+Down": "temp_cool",
    "Left": "rotate_ccw",
    "Right": "rotate_cw",
    "Ctrl+Left": "prev_file",
    "Ctrl+Right": "next_file",
    # The three custom keys: X and C, and Return, which stays the Enter key.
    "X": "copy_with_bounds",
    "C": "paste",
}
# The panel's Mirror key sends Shift alone, which no key sequence can hold.
SMARTCONVERT_SHIFT_TAP = "pick_dust"


def smartconvert_layer(bindings: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """The bindings with the panel's keys taken over, and the panel's key -> action map."""
    from PyQt6.QtGui import QKeySequence

    from negpy.desktop.view.shortcut_registry import clash_scope

    def norm(key: str) -> str:
        return QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)

    aliases = dict(SMARTCONVERT_PANEL)
    taken = {norm(key) for key in aliases}
    effective = {
        action: ("" if key and clash_scope(action, key) == "main" and norm(key) in taken else key) for action, key in bindings.items()
    }
    return effective, aliases
