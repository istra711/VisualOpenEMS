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
    # "keep" = only fit the camera (the historic Reset view behaviour)
    assert AppSettings().field_viewer_view == "keep"
    path = tmp_path / "gui_settings.json"
    s = AppSettings()
    s.field_viewer_view = "iso"
    s.save(path)
    assert AppSettings.load(path).field_viewer_view == "iso"
    # old settings files without the key load with the default
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"work_dir": "x"}', encoding="utf-8")
    assert AppSettings.load(legacy).field_viewer_view == "keep"


def test_field_viewer_vectors_default_and_roundtrip(tmp_path):
    # vector glyphs are opt-in; the viewer fills in its own defaults
    assert AppSettings().field_viewer_vectors == {}
    path = tmp_path / "gui_settings.json"
    opts = {"enabled": True, "size": 2.0, "max_arrows": 500, "normalize": False}
    s = AppSettings()
    s.field_viewer_vectors = dict(opts)
    s.save(path)
    assert AppSettings.load(path).field_viewer_vectors == opts


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
