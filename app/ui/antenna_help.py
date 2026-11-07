"""Detailed, illustrated help for the Antenna tab.

The help explains the NF2FF angles on actual-looking antenna patterns:
theta/phi, the E-/H-plane and azimuth cuts, and how to read the polar dBi
plot (HPBW, front-to-back ratio, 3D radius/color).  Images are generated in
memory with matplotlib Agg; no external files are needed.
"""
from __future__ import annotations

import io
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


def _pixmap(fig, width: int = 900) -> QPixmap:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    buf = io.BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm.scaled(
        width,
        10_000,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _sample_pattern(theta, phi):
    """Illustrative patch-like far field: broad +z lobe plus a back lobe.

    Not a simulated result — it only provides readable curves for the help
    diagrams.  The real Antenna tab plots the loaded NF2FF result.
    """
    import numpy as np

    main = 10.0 * np.exp(-((theta / 0.55) ** 2))
    back = -9.0 * np.exp(-(((np.pi - theta) / 0.55) ** 2))
    ripple = 2.0 * np.cos(2.0 * phi) * np.sin(theta) ** 2
    return main + back + ripple


def _arrow(ax, xy, text: str, color: str = "black") -> None:
    ax.annotate(
        "",
        xy=xy,
        xytext=(0.0, 0.0),
        arrowprops=dict(arrowstyle="->", lw=1.5, color=color),
    )
    ax.text(xy[0] * 1.08, xy[1] * 1.08, text, color=color, fontsize=11, weight="bold")


def pattern_figure():
    """A 3D pattern surface with the theta/phi directions marked."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig = plt.figure(figsize=(7.8, 4.6))
    ax = fig.add_subplot(111, projection="3d")
    th = np.linspace(0, np.pi, 55)
    ph = np.linspace(0, 2 * np.pi, 109)
    T, P = np.meshgrid(th, ph, indexing="ij")
    db = _sample_pattern(T, P)
    R = np.clip((db + 22.0) / 32.0, 0.08, 1.0)
    X = R * np.sin(T) * np.cos(P)
    Y = R * np.sin(T) * np.sin(P)
    Z = R * np.cos(T)
    cmap = plt.get_cmap("viridis")
    norm = plt.Normalize(vmin=-30.0, vmax=10.0)
    ax.plot_surface(
        X, Y, Z,
        facecolors=cmap(norm(db)),
        rstride=1, cstride=1, linewidth=0, antialiased=True, shade=False,
        alpha=0.95,
    )
    # axes
    for vec, label, color in (
        ((1.25, 0, 0), "x", "black"),
        ((0, 1.25, 0), "y", "black"),
        ((0, 0, 1.25), "+z  (θ = 0°)", "tab:blue"),
    ):
        ax.quiver(0, 0, 0, *vec, color=color, arrow_length_ratio=0.08, lw=1.4)
        ax.text(vec[0] * 1.08, vec[1] * 1.08, vec[2] * 1.08, label,
                color=color, fontsize=9, weight="bold")
    # beam direction and angle annotations
    thb, phb = math.radians(35), math.radians(45)
    rb = 1.12
    bx, by, bz = rb * math.sin(thb) * math.cos(phb), rb * math.sin(thb) * math.sin(phb), rb * math.cos(thb)
    ax.quiver(0, 0, 0, bx, by, bz, color="tab:red", arrow_length_ratio=0.08, lw=1.6)
    # theta arc in the beam's meridian
    t = np.linspace(0, thb, 40)
    ax.plot(
        1.08 * np.sin(t) * np.cos(phb),
        1.08 * np.sin(t) * np.sin(phb),
        1.08 * np.cos(t),
        color="tab:red", lw=1.6, zorder=10,
    )
    ax.text(
        1.08 * np.sin(thb / 2) * np.cos(phb) * 1.18,
        1.08 * np.sin(thb / 2) * np.sin(phb) * 1.18,
        1.10 * np.cos(thb / 2),
        "θ", color="tab:red", fontsize=15, weight="bold", zorder=10,
    )
    # phi arc in the xy plane
    p = np.linspace(0, phb, 40)
    ax.plot(1.08 * np.cos(p), 1.08 * np.sin(p), 0 * p,
            color="tab:orange", lw=1.6, zorder=10)
    ax.text(1.12 * np.cos(phb / 2), 1.12 * np.sin(phb / 2), 0.02,
            "φ", color="tab:orange", fontsize=15, weight="bold", zorder=10)
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-1.25, 1.25)
    ax.set_zlim(-0.85, 1.2)
    ax.set_box_aspect((1, 1, 0.95))
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_zticks([])
    ax.view_init(elev=26, azim=34)
    ax.set_title("Illustrative 3D pattern: θ from +z, φ in the xy plane", fontsize=10)
    fig.tight_layout()
    return _pixmap(fig)


def angle_figure():
    """Side and top views: exact definitions of theta and phi."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Arc

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.2, 3.7))
    for ax in (ax1, ax2):
        ax.set_aspect("equal")
        ax.set_xlim(-1.15, 1.15)
        ax.set_ylim(-1.35, 1.15)
        ax.axis("off")

    ax1.set_title("θ — polar angle / elevation", fontsize=11, weight="bold")
    _arrow(ax1, (0.0, 1.0), "+z (θ = 0°, boresight)", "tab:blue")
    _arrow(ax1, (1.0, 0.0), "+x", "black")
    ax1.plot([0], [0], "ko", ms=4)
    th = math.radians(55)
    ax1.annotate(
        "",
        xy=(0.92 * math.sin(th), 0.92 * math.cos(th)),
        xytext=(0.0, 0.0),
        arrowprops=dict(arrowstyle="->", lw=2.0, color="tab:red"),
    )
    ax1.add_patch(Arc((0, 0), 0.9, 0.9, theta1=90 - 55, theta2=90,
                      color="tab:red", lw=1.4))
    ax1.text(0.22, 0.52, "θ", color="tab:red", fontsize=15, weight="bold")
    ax1.text(
        0.0, -1.12,
        "θ = 0°: +z (zenith / boresight)\n"
        "θ = 90°: xy plane (horizon)\n"
        "θ = 180°: −z (nadir)",
        ha="center", va="bottom", fontsize=9.5,
        bbox=dict(boxstyle="round,pad=0.3", fc="#f6f6f6", ec="#bbbbbb"),
    )

    ax2.set_title("φ — azimuth", fontsize=11, weight="bold")
    _arrow(ax2, (1.0, 0.0), "+x (φ = 0°)", "black")
    _arrow(ax2, (0.0, 1.0), "+y (φ = 90°)", "tab:green")
    ax2.add_patch(Arc((0, 0), 1.25, 1.25, theta1=0, theta2=40,
                      color="tab:orange", lw=1.6))
    ph = math.radians(40)
    ax2.annotate(
        "",
        xy=(0.92 * math.cos(ph), 0.92 * math.sin(ph)),
        xytext=(0.0, 0.0),
        arrowprops=dict(arrowstyle="->", lw=2.0, color="tab:orange"),
    )
    ax2.text(0.50, 0.20, "φ", color="tab:orange", fontsize=15, weight="bold")
    ax2.text(
        0.0, -1.12,
        "φ is measured in the xy plane from +x toward +y.\n"
        "φ = 0°: +x   •   φ = 90°: +y\n"
        "φ = 180°: −x  •   φ = 270°: −y",
        ha="center", va="bottom", fontsize=9.5,
        bbox=dict(boxstyle="round,pad=0.3", fc="#f6f6f6", ec="#bbbbbb"),
    )
    fig.tight_layout()
    return _pixmap(fig)


def cuts_figure():
    """Three polar cuts using the same illustrative pattern."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(
        1, 3, figsize=(9.4, 3.5), subplot_kw={"projection": "polar"}
    )
    titles = [
        "H-plane  φ = 0°\n(xz cut)",
        "E-plane  φ = 90°\n(yz cut)",
        "Azimuth  θ = 90°\n(xy cut)",
    ]
    for ax, title in zip(axes, titles):
        ax.set_title(title, fontsize=9.5, weight="bold")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_rlim(-35, 10)
        ax.set_rticks([-30, -20, -10, 0, 10])
        ax.set_rlabel_position(22)
        ax.grid(True, alpha=0.3)

    t = np.linspace(0, 2 * np.pi, 721)
    t_abs = np.minimum(t, 2 * np.pi - t)
    axes[0].plot(t, _sample_pattern(t_abs, 0.0), lw=1.6, color="tab:blue")
    axes[1].plot(t, _sample_pattern(t_abs, np.pi / 2), lw=1.6, color="tab:red")
    axes[2].plot(t, _sample_pattern(np.pi / 2, t), lw=1.6, color="tab:green")
    for ax, note in zip(
        axes,
        ("angle = θ\n0° = +z", "angle = θ\n0° = +z", "angle = φ\n0° = +x"),
    ):
        ax.text(0.5, -0.12, note, transform=ax.transAxes, ha="center", va="top",
                fontsize=8)
    fig.tight_layout()
    return _pixmap(fig)


def polar_figure():
    """How to read one polar dBi cut (angle = theta, radius = dBi)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    th = np.linspace(0, 2 * np.pi, 721)
    th_abs = np.minimum(th, 2 * np.pi - th)
    db = _sample_pattern(th_abs, 0.0)
    db = np.maximum(db, -32.0)

    fig = plt.figure(figsize=(6.4, 5.8))
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.plot(th, db, lw=2.0, color="tab:blue")
    ax.set_rlim(-35, 10)
    ax.set_rticks([-30, -20, -10, 0, 10])
    ax.set_rlabel_position(22)
    ax.grid(True, alpha=0.35)
    ax.set_title("Polar cut: angle = θ, radius = dBi", pad=26, fontsize=11,
                 weight="bold")
    ax.text(0.98, 0.50, "θ = 90°", ha="right", va="center",
            transform=ax.transAxes, fontsize=9)
    ax.text(0.0, -0.08, "θ = 180°\n(back side)", ha="center", va="top",
            transform=ax.transAxes, fontsize=9)
    # approximate -3 dB point of the illustrative main lobe
    peak = float(np.nanmax(db))
    idx = int(np.argmax(db > peak - 3.0))
    ax.annotate(
        "−3 dB from peak\n= HPBW boundary",
        xy=(th[idx], peak - 3.0),
        xytext=(0.54, 0.72),
        textcoords="axes fraction",
        arrowprops=dict(arrowstyle="->", lw=1.1, color="tab:red"),
        fontsize=9,
        color="tab:red",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="tab:red", alpha=0.9),
    )
    fig.tight_layout()
    return _pixmap(fig)


class _FitTextBrowser(QTextBrowser):
    """Rich text that grows to its content; the outer page scrolls instead."""

    def __init__(self) -> None:
        super().__init__()
        self.setOpenExternalLinks(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit()

    def _fit(self, *_args) -> None:
        width = self.viewport().width()
        if width < 60:
            return
        self.document().setTextWidth(width)
        height = int(self.document().size().height()) + 8
        height = max(120, height)
        if abs(height - self.height()) > 1:
            self.setFixedHeight(height)


class _ScaledImage(QLabel):
    """Keep the diagram readable when the dialog is made smaller."""

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self._source = pixmap
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(300)
        self.setMinimumHeight(170)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(300, self.width())
        pm = self._source.scaled(
            width,
            10_000,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pm)
        # QLabel must get the full scaled height, otherwise tall diagrams
        # (the polar plot especially) are clipped top/bottom.  The outer
        # scroll area then scrolls image + text together.
        target = max(170, pm.height())
        if self.height() != target:
            self.setFixedHeight(target)


class AntennaHelpDialog(QDialog):
    """Tabbed antenna-pattern help with diagrams and concise explanations."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Antenna patterns — angles, cuts and dBi")
        self.resize(760, 560)
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        tabs.addTab(
            self._tab(
                pattern_figure(),
                "<h3>How the far-field angles define the pattern</h3>"
                "<p>The NF2FF result is a far field on a sphere around the "
                "integration-box centre. Every direction is identified by two "
                "angles:</p>"
                "<ul>"
                "<li><b>θ (theta, polar angle):</b> angle from the positive "
                "<b>+z axis</b>. θ = 0° is zenith / boresight, θ = 90° is the "
                "xy plane (horizon), θ = 180° points toward −z.</li>"
                "<li><b>φ (phi, azimuth):</b> angle in the xy plane, measured "
                "from <b>+x</b> toward <b>+y</b>. φ = 0° is +x, φ = 90° is +y, "
                "φ = 180° is −x, φ = 270° is −y.</li>"
                "</ul>"
                "<p>The surface shows an illustrative pattern: radius and colour "
                "encode the far-field level; the red arrow is one beam direction "
                "with its θ and φ. θ and φ are fixed to the model coordinate "
                "system, not to the screen. If the antenna is rotated in the "
                "model, the angles rotate with it.</p>",
            ),
            "Angles & 3D pattern",
        )

        tabs.addTab(
            self._tab(
                angle_figure(),
                "<h3>Exact θ and φ definitions</h3>"
                "<p><b>θ runs along a meridian</b> from +z to −z. A cut at fixed "
                "φ contains the full meridian; plotting it on a 360° polar axis "
                "shows θ = 0…180° on one half and the φ+180° half on the other. "
                "That is why the polar plots can show 0° at the top and 180° at "
                "the bottom while θ itself only goes to 180°.</p>"
                "<p><b>φ runs around the z axis</b> in the xy plane. At fixed "
                "θ (for example the azimuth cut θ = 90°), it is the angle around "
                "the antenna. A positive φ turns from +x toward +y.</p>"
                "<p>The NF2FF box centre is the phase reference. Moving the "
                "antenna relative to that centre changes phase and beam "
                "direction, not the meaning of the angles.</p>",
            ),
            "θ / φ",
        )

        tabs.addTab(
            self._tab(
                cuts_figure(),
                "<h3>E-plane, H-plane and azimuth cuts</h3>"
                "<p>A 2D cut is a great circle through the pattern centre. The "
                "Antenna tab labels mean:</p>"
                "<ul>"
                "<li><b>H-plane (φ = 0°):</b> xz plane. θ runs from +z "
                "(0°) toward +x (90°).</li>"
                "<li><b>E-plane (φ = 90°):</b> yz plane. θ runs from +z "
                "(0°) toward +y (90°).</li>"
                "<li><b>Azimuth (θ = 90°):</b> xy plane. The angle around the "
                "plot is φ.</li>"
                "</ul>"
                "<p>These are not separate simulations: every cut is read from "
                "the same complex NF2FF field. E/H are the natural principal "
                "planes for a linearly polarised antenna. For circular "
                "polarisation both planes can look similar; then the axial-ratio "
                "cut and several φ cuts are more informative.</p>",
            ),
            "Plane cuts",
        )

        tabs.addTab(
            self._tab(
                polar_figure(),
                "<h3>Reading a polar dBi plot</h3>"
                "<ul>"
                "<li><b>Angle:</b> the rim is θ (0° top, 90° right, 180° bottom, "
                "270° left). The curve is the far-field magnitude in that "
                "direction.</li>"
                "<li><b>Radius:</b> distance from the centre is the level in "
                "<b>dBi</b>. The concentric rings are the labelled dBi steps. "
                "A larger radius means more gain.</li>"
                "<li><b>HPBW:</b> half-power beamwidth — the angular width "
                "around the peak where the level is at most 3 dB below the peak. "
                "The Antenna tab can draw this boundary and reports the value.</li>"
                "<li><b>F/B (VRV):</b> front-to-back ratio — peak level minus the "
                "level 180° away from the peak. A large value means low "
                "back radiation.</li>"
                "</ul>"
                "<p><b>3D Pattern:</b> surface radius and colour are both mapped "
                "from the selected Dmax or Gain field. The “dBi min” slider only "
                "changes the visible window; it does not change the result. "
                "<b>Gain vs Frequency</b> and <b>Radiated Efficiency</b> show "
                "the same NF2FF evaluation over frequency.</p>"
                "<p>All help diagrams use an illustrative pattern; the Antenna "
                "tab always plots the loaded nf2ff.json.</p>",
            ),
            "Polar dBi",
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _tab(pixmap: QPixmap, text: str) -> QWidget:
        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(8, 8, 8, 8)
        img = _ScaledImage(pixmap)
        v.addWidget(img)
        browser = _FitTextBrowser()
        browser.setHtml(text)
        v.addWidget(browser)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        return scroll
