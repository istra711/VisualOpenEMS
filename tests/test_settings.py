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


def test_mesh_viewer_objects_default_and_roundtrip(tmp_path):
    # empty dict = per-kind colours, the viewer applies its own 30 % default
    assert AppSettings().mesh_viewer_objects == {}
    path = tmp_path / "gui_settings.json"
    style = {"color": [0.1, 0.8, 0.2], "transparency": 75}
    s = AppSettings()
    s.mesh_viewer_objects = dict(style)
    s.save(path)
    assert AppSettings.load(path).mesh_viewer_objects == style
    # "kind colours" is stored explicitly as color=None
    s.mesh_viewer_objects = {"color": None, "transparency": 30}
    s.save(path)
    assert AppSettings.load(path).mesh_viewer_objects == {
        "color": None,
        "transparency": 30,
    }
