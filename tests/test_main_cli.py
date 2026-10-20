"""``app.main.project_arg`` — open a project file passed on the command line."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_project_arg_picks_existing_file():
    from app.main import project_arg

    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "demo.json"
        proj.write_text("{}", encoding="utf-8")
        assert project_arg([str(proj)]) == proj
        # Qt option-like arguments are skipped, the project still wins
        assert project_arg(["-style", "fusion", str(proj)]) == proj
        # a directory / a missing path is ignored (GUI starts normally)
        assert project_arg([td]) is None
        assert project_arg([str(Path(td) / "nope.json")]) is None
        assert project_arg([]) is None
