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
