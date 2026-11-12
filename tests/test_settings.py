"""AppSettings defaults and round-trip for the Field Viewer options."""

from __future__ import annotations

from app.settings import AppSettings


def test_field_viewer_autoplay_defaults_off():
    assert AppSettings().field_viewer_autoplay is False


def test_field_viewer_autoplay_roundtrip(tmp_path):
    path = tmp_path / "gui_settings.json"
    s = AppSettings()
    s.field_viewer_autoplay = True
    s.save(path)

    assert AppSettings.load(path).field_viewer_autoplay is True


def test_field_viewer_view_default_and_roundtrip(tmp_path):
    # "iso" = isometric view when a field is opened (the historic default was
    # "keep", which only fitted the camera and kept whatever direction was set)
    assert AppSettings().field_viewer_view == "iso"
    path = tmp_path / "gui_settings.json"
    s = AppSettings()
    s.field_viewer_view = "z+"
    s.save(path)
    assert AppSettings.load(path).field_viewer_view == "z+"
    # old settings files without the key load with the default
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_view == "iso"


def test_field_viewer_mouse_default_and_roundtrip(tmp_path):
    # optional mouse options; the viewer fills in DEFAULT_MOUSE
    assert AppSettings().field_viewer_mouse == {}
    path = tmp_path / "gui_settings.json"
    opts = {
        "style": "joystick",
        "motion_factor": 2.0,
        "wheel_factor": 0.5,
        "invert_wheel": True,
        "swap_buttons": True,
        "right_button_pan": False,
    }
    s = AppSettings()
    s.field_viewer_mouse = dict(opts)
    s.save(path)
    assert AppSettings.load(path).field_viewer_mouse == opts
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_mouse == {}


def test_field_viewer_vectors_default_and_roundtrip(tmp_path):
    # vector glyphs are opt-in; the viewer fills in its own defaults
    assert AppSettings().field_viewer_vectors == {}
    path = tmp_path / "gui_settings.json"
    opts = {"enabled": True, "size": 2.0, "max_arrows": 500, "normalize": False}
    s = AppSettings()
    s.field_viewer_vectors = dict(opts)
    s.save(path)
    assert AppSettings.load(path).field_viewer_vectors == opts


def test_field_viewer_streamlines_default_and_roundtrip(tmp_path):
    # appearance options are optional; the viewer fills in DEFAULT_STREAMLINES
    assert AppSettings().field_viewer_streamlines == {}
    path = tmp_path / "gui_settings.json"
    opts = {
        "enabled": True,
        "representation": "tube",
        "radius_pct": 0.35,
        "sides": 12,
        "line_width": 2.0,
        "opacity": 70,
        "color_mode": "speed",
        "color": [1.0, 0.5, 0.0],
        "seeds": 5,
    }
    s = AppSettings()
    s.field_viewer_streamlines = dict(opts)
    s.save(path)
    assert AppSettings.load(path).field_viewer_streamlines == opts
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_streamlines == {}


def test_field_viewer_color_scale_default_and_roundtrip(tmp_path):
    # the colour scale is optional; the viewer starts from its own defaults
    assert AppSettings().field_viewer_color_scale == {}
    path = tmp_path / "gui_settings.json"
    opts = {"cmap": "viridis", "reverse": True, "steps": 64}
    s = AppSettings()
    s.field_viewer_color_scale = dict(opts)
    s.save(path)
    assert AppSettings.load(path).field_viewer_color_scale == opts
    # old settings files without the key load with the empty default
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_color_scale == {}


def test_field_viewer_title_default_and_roundtrip(tmp_path):
    # bar-title options are optional; the viewer fills in DEFAULT_BAR_TITLE
    assert AppSettings().field_viewer_title == {}
    path = tmp_path / "gui_settings.json"
    opts = {"left": True, "x": 0.8, "y": 0.25, "size": 18, "rotation": 90}
    s = AppSettings()
    s.field_viewer_title = dict(opts)
    s.save(path)
    assert AppSettings.load(path).field_viewer_title == opts
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_title == {}


def test_field_viewer_color_bar_default_and_roundtrip(tmp_path):
    # bar geometry/font are optional; the viewer fills in DEFAULT_COLOR_BAR
    assert AppSettings().field_viewer_color_bar == {}
    path = tmp_path / "gui_settings.json"
    opts = {
        "x": 0.7,
        "y": 0.1,
        "width": 0.2,
        "height": 0.6,
        "font": "Times",
        "font_size": 14,
    }
    s = AppSettings()
    s.field_viewer_color_bar = dict(opts)
    s.save(path)
    assert AppSettings.load(path).field_viewer_color_bar == opts
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_color_bar == {}


def test_field_viewer_layout_default_and_roundtrip(tmp_path):
    # B = docked display panel is the default; C = minimal + Display popup
    assert AppSettings().field_viewer_layout == "panel"
    path = tmp_path / "gui_settings.json"
    s = AppSettings()
    s.field_viewer_layout = "minimal"
    s.save(path)
    assert AppSettings.load(path).field_viewer_layout == "minimal"
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_layout == "panel"


def test_field_viewer_playback_defaults_and_roundtrip(tmp_path):
    s = AppSettings()
    assert s.field_viewer_fps == 4
    assert s.field_viewer_loop is True
    path = tmp_path / "gui_settings.json"
    s.field_viewer_fps = 12
    s.field_viewer_loop = False
    s.save(path)
    loaded = AppSettings.load(path)
    assert loaded.field_viewer_fps == 12
    assert loaded.field_viewer_loop is False


def test_design_mesh_color_default_and_roundtrip(tmp_path):
    # the 3D Design mesh plane is white by default (Viewer settings)
    assert AppSettings().design_mesh_color == "#ffffff"
    path = tmp_path / "gui_settings.json"
    s = AppSettings()
    s.design_mesh_color = "#ffcc00"
    s.save(path)
    assert AppSettings.load(path).design_mesh_color == "#ffcc00"
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).design_mesh_color == "#ffffff"


def test_antenna_3d_quality_default_and_roundtrip(tmp_path):
    # balanced = shaded surface without the heavy high-resolution grid
    assert AppSettings().antenna_3d_quality == "balanced"
    path = tmp_path / "gui_settings.json"
    s = AppSettings()
    s.antenna_3d_quality = "high"
    s.save(path)
    assert AppSettings.load(path).antenna_3d_quality == "high"
    # old settings files without the key load with the balanced default
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).antenna_3d_quality == "balanced"


def test_window_layout_state_roundtrip(tmp_path):
    s = AppSettings()
    assert s.main_splitter_sizes == [] and s.log_splitter_sizes == []
    assert s.current_tab == ""
    path = tmp_path / "gui_settings.json"
    s.main_splitter_sizes = [250, 750]
    s.log_splitter_sizes = [500, 200]
    s.current_tab = "Field Viewer"
    s.viewer_settings_tab = 2
    s.dialog_geometry = {"viewer_settings": "abc"}
    s.save(path)
    loaded = AppSettings.load(path)
    assert loaded.main_splitter_sizes == [250, 750]
    assert loaded.log_splitter_sizes == [500, 200]
    assert loaded.current_tab == "Field Viewer"
    assert loaded.viewer_settings_tab == 2
    assert loaded.dialog_geometry == {"viewer_settings": "abc"}


def test_variable_autocomplete_default_and_roundtrip(tmp_path):
    assert AppSettings().variable_autocomplete is True
    path = tmp_path / "gui_settings.json"
    s = AppSettings()
    s.variable_autocomplete = False
    s.save(path)
    assert AppSettings.load(path).variable_autocomplete is False
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).variable_autocomplete is True
