"""Compatibility probes for the installed openEMS Python package."""

from __future__ import annotations


def rect_wg_second_axis_bug() -> bool:
    """True when ``RectWGPort`` builds ``name_PP`` from the *first* axis.

    openEMS 0.0.35 formats the second transverse coordinate with
    ``xyz[self.ny_P]`` instead of ``xyz[self.ny_PP]``, so a mode with ``N > 0``
    (TE01/TE11) varies along the wrong axis. The excitation and the mode-match
    probes then miss the field: the port reports isolation while the power
    physically leaves through that arm. TE10 (``N == 0``) is unaffected.
    """
    try:
        import inspect

        from openEMS.ports import RectWGPort

        src = inspect.getsource(RectWGPort.__init__)
    except Exception:  # noqa: BLE001 - no openEMS, or a rewritten class
        return False
    return "name_PP = '({}-{})'.format(xyz[self.ny_P], self.start[self.ny_P])" in src
