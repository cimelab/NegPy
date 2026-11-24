"""The NegPy and Lightroom shortcut sets."""

from PyQt6.QtGui import QKeySequence

from negpy.desktop.view.shortcut_presets import LIGHTROOM, PRESETS, matching_preset, preset_bindings
from negpy.desktop.view.shortcut_registry import REGISTRY, clash_scope, default_bindings, merge_bindings


def test_every_lightroom_action_is_a_registry_action():
    assert [action_id for action_id in LIGHTROOM if action_id not in REGISTRY] == []


def test_the_negpy_set_is_the_defaults():
    assert preset_bindings("negpy") == default_bindings()


def test_no_two_actions_share_a_key_in_any_set(qapp):
    # Two actions on one key makes Qt fire activatedAmbiguously and both go dead.
    for name in PRESETS:
        keys = [(clash_scope(action_id, key), QKeySequence(key).toString()) for action_id, key in preset_bindings(name).items() if key]
        assert len(keys) == len(set(keys)), name


def test_every_lightroom_key_parses(qapp):
    bad = [key for key in LIGHTROOM.values() if key and not QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)]
    assert bad == []


def test_lightroom_keys_land_where_lightroom_has_them():
    bindings = preset_bindings("lightroom")
    assert bindings["toggle_light_table"] == "G"
    assert bindings["manual_crop"] == "R"
    assert bindings["pick_dust"] == "Q"
    assert bindings["toggle_keep"] == "P"
    assert bindings["toggle_reject"] == "X"
    assert bindings["copy"] == "Ctrl+Shift+C"
    assert bindings["paste"] == "Ctrl+Shift+V"
    assert bindings["export_preferences"] == "Ctrl+Shift+E"


def test_a_negpy_default_on_a_lightroom_key_is_left_unbound():
    bindings = preset_bindings("lightroom")
    # P was the embedded-preview peek and M the flat peek; Lightroom gives them to Pick and the gradient.
    assert bindings["toggle_embedded_peek"] == ""
    assert bindings["toggle_flat_peek"] == ""


def test_the_live_view_keeps_its_own_keys():
    """R, M and Z are live view keys too, but in another window, so they do not clash."""
    bindings = preset_bindings("lightroom")
    assert bindings["live_view_retake"] == REGISTRY["live_view_retake"].default_key


def test_matching_preset_names_the_set_in_use():
    assert matching_preset(default_bindings()) == "negpy"
    assert matching_preset(preset_bindings("lightroom")) == "lightroom"
    assert matching_preset(merge_bindings({"copy": "Ctrl+Alt+Shift+K"})) == ""


def test_every_smartconvert_panel_action_is_a_registry_action():
    from negpy.desktop.view.shortcut_presets import SAME_AS_COPY_KEY, SMARTCONVERT_PANEL, SMARTCONVERT_SHIFT_TAP

    actions = [a for a in SMARTCONVERT_PANEL.values() if a != SAME_AS_COPY_KEY] + [SMARTCONVERT_SHIFT_TAP]
    assert [a for a in actions if a not in REGISTRY] == []


def test_the_panel_layer_frees_its_keys_in_every_set(qapp):
    """Two live shortcuts on one key both go dead, so the set gives up every key the panel sends."""
    from negpy.desktop.view.shortcut_presets import smartconvert_layer

    for name in PRESETS:
        live, panel = smartconvert_layer(preset_bindings(name))
        panel_keys = {QKeySequence(k).toString() for k in panel}
        clashes = [a for a, k in live.items() if k and clash_scope(a, k) == "main" and QKeySequence(k).toString() in panel_keys]
        assert clashes == [], name


def test_the_panel_takes_the_keys_it_sends():
    from negpy.desktop.view.shortcut_presets import smartconvert_layer

    live, panel = smartconvert_layer(default_bindings())
    assert panel["Q"] == "cyan_inc" and panel["A"] == "cyan_dec"
    assert panel["Up"] == "density_up" and panel["Alt+Up"] == "grade_up" and panel["Ctrl+Up"] == "temp_warm"
    assert panel["Left"] == "rotate_ccw" and panel["Ctrl+Right"] == "next_file"
    assert panel["C"] == "paste"
    # NegPy's density key Q and file key Left give way to the panel; rotate keeps its own [ too.
    assert live["density_up"] == "" and live["prev_file"] == ""
    assert live["rotate_ccw"] == REGISTRY["rotate_ccw"].default_key


def test_the_panels_x_key_runs_what_ctrl_shift_c_runs_in_the_set_in_use():
    from negpy.desktop.view.shortcut_presets import smartconvert_layer

    assert smartconvert_layer(preset_bindings("negpy"))[1]["X"] == "copy_with_bounds"
    assert smartconvert_layer(preset_bindings("lightroom"))[1]["X"] == "copy"


def test_return_stays_the_enter_key():
    from negpy.desktop.view.shortcut_presets import SMARTCONVERT_PANEL

    assert not any(QKeySequence(k).toString() in ("Return", "Enter") for k in SMARTCONVERT_PANEL)
