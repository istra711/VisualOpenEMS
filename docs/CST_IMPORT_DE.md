# CST-Studio-Import (Variante A)

VisualopenEMS liest **kein** natives CST-Projekt (`.cst`). Stattdessen wird ein
kleines Austauschpaket importiert:

```
mein_projekt_cst_package/
├─ manifest.json
└─ stl/
   ├─ PEC__trace1.stl
   ├─ FR4__substrate.stl
   └─ ...
```

Ein ZIP mit demselben Inhalt wird ebenfalls unterstützt. `File → Import CST
Studio Package…` und `Geometry → Import CST Studio Package…` öffnen den
Importdialog. Im Mesh Viewer werden die STL-Solids mit ihrer echten
Dreieckskontur dargestellt (Schnitt durch die Slice-Ebene bzw. projizierte
Dreiecke in der Übersicht), nicht mehr nur als Bounding-Box-Rechtecke.

## Export aus CST

Das Makro `app/freecad_io/cst_export_macro.bas` in CST Studio Suite 2021
öffnen/importieren und mit geöffnetem 3D-Projekt starten. Es fragt nach einem
Zielordner und schreibt `manifest.json` plus eine STL pro Solid.

Wichtig:

- CST exportiert STL standardmäßig in **Metern**. Das Makro setzt
  `ExportFileUnits "m"`; das Manifest deklariert `units.geometry = "m"`.
- Objektnamen bleiben als `Komponente/Solid` erhalten (z. B. `PEC/trace1`).
- Das Makro liest Materialname, Typ, εr, µr, Leitfähigkeit und Farbe. Die
  **Verlusttangente (tan δ)** lässt sich über die CST-VBA-Abfragen nicht
  zuverlässig auslesen; das Manifest enthält deshalb nur die Leitfähigkeit.
  Die Warnungsliste im Manifest weist darauf hin. Nach dem Import ggf. tan δ
  im Materialdialog ergänzen.
- Diskrete Ports werden mit Start/Stop exportiert. Für Waveguide-Ports liefert
  CST die Portbox erst nach einem aufgebauten Mesh; ohne Mesh wird der Port mit
  einer Warnung übersprungen. In diesem Fall in CST das Mesh bauen und das
  Makro erneut ausführen.
- Das Makro ist bewusst konservativ: nicht abfragbare Geometrie/Ports werden
  nicht geraten. Die CST-PML-Schichtzahl wird ebenfalls nicht abgefragt; der
  Import verwendet die aktuelle PML-Größe des Projekts (Standard 8) und das
  Makro schreibt einen Hinweis in `warnings`.

## Manifest-Schema

Alle Koordinaten und Längen im Manifest werden mit `units.geometry` angegeben.
Das Makro schreibt für STL und Ports konsistent `m`.

```json
{
  "format": "cst-export-v1",
  "project": "demo",
  "units": { "geometry": "m", "frequency": "GHz" },
  "frequency": { "start": 1.0, "stop": 6.0, "unit": "GHz" },
  "boundaries": {
    "xmin": "electric",
    "xmax": "electric",
    "ymin": "magnetic",
    "ymax": "magnetic",
    "zmin": "open",
    "zmax": "pml",
    "pml_size": 8
  },
  "domain": {
    "calculation_box": {
      "min": [-0.005, -0.003, -0.001],
      "max": [0.025, 0.013, 0.006]
    },
    "structure_box": {
      "min": [0.0, 0.0, 0.0],
      "max": [0.020, 0.010, 0.001035]
    }
  },
  "materials": [
    { "name": "PEC", "kind": "pec" },
    {
      "name": "FR4",
      "kind": "dielectric",
      "epsilon": 4.4,
      "permeability": 1.0,
      "kappa": 0.0,
      "tand": 0.02
    }
  ],
  "objects": [
    {
      "name": "PEC/trace1",
      "component": "PEC",
      "material": "PEC",
      "kind": "solid",
      "file": "stl/PEC__trace1.stl"
    },
    {
      "name": "FR4/substrate",
      "component": "FR4",
      "material": "FR4",
      "kind": "solid",
      "file": "stl/FR4__substrate.stl"
    }
  ],
  "ports": [
    {
      "name": "p1",
      "type": "lumped",
      "start": [0.01, 0.004, 0.001],
      "stop": [0.01, 0.006, 0.001],
      "direction": "y",
      "resistance": 50,
      "excite": true
    },
    {
      "name": "wg1",
      "type": "rect_waveguide",
      "start": [0.0, 0.0, 0.0],
      "stop": [0.02286, 0.01016, 0.0],
      "direction": "x",
      "wg_mode": "TE10",
      "excite": false
    }
  ],
  "warnings": [
    "Optional: free-text warnings shown in the import dialog."
  ]
}
```

### Einheiten

- `units.geometry`: `m`, `cm`, `mm`, `um`, `nm`, `in`, `ft`, `mil` (auch
  `meter`, `millimeter` …).
- `units.frequency`: `Hz`, `kHz`, `MHz`, `GHz`, `THz`, `PHz`.
- Zieldateien werden auf die Zeichnungseinheit des Projekts skaliert. STL und
  Ports werden getrennt behandelt, aber beide über `units.geometry`.

### Rechenbereich / PEC-Wandabstand

Das Makro exportiert mit `Boundary.GetCalculationBox` und
`Boundary.GetStructureBox` die CST-Rechenbox und die Struktur-Box. Der Import
setzt daraus die sechs `air_margin`-Werte des Projekts (xmin, xmax, ymin, ymax,
zmin, zmax). Das ist besonders wichtig, wenn es **keinen expliziten
Ground-Layer** gibt und eine PEC-Randbedingung als Ground wirkt: Dann muss die
PEC-Wand exakt an der CST-Position bleiben und darf nicht durch den
automatischen λ/4-Abstand nach außen geschoben werden.

Fehlt `domain` im Manifest (altes Paket), bleibt der bisherige
Projekt-Air-Margin (`auto`) erhalten; für eine CST-treue PEC-Wand das Paket
mit dem aktualisierten Makro neu exportieren. Bei PML-Rändern enthält die
CST-Rechenbox ggf. die PML-Region; die übernommenen Abstände sind dann als
äußere Rechenbox zu verstehen und sollten bei Bedarf im Simulation-Parameter-
Dialog geprüft werden.

### Werte in VisualopenEMS prüfen und ändern

- **Simulation → Simulation Parameters… → Boundary conditions**:
  - `Air margin auto` (λ/4)
  - `Global air margin: all six sides equal`
  - sechs Einzelfelder `Air margin xmin/xmax/ymin/ymax/zmin/zmax`
- Die Einzelfelder sind automatisch deaktiviert, solange Auto oder Global aktiv
  ist. Bei einem CST-Import stehen dort die aus der CST-Rechenbox berechneten
  Werte (z. B. zmin=0 für eine PEC-Grundfläche am Platinenrand).
- **PCB simulation**:
  - *Apply sensible PCB defaults (open board)*: 10 mm in x/y, zmin=0,
    zmax=10 mm; Ränder x/y/zmax MUR, zmin PEC.
  - *PCB in sheet-metal enclosure*: Metallwände PEC;
    xmin/xmax/ymin/ymax/zmin = 0, nur der zmax-Abstand kommt aus
    *Lid height above PCB top (zmax)*. Flächen mit einem MSL-Port bekommen
    **PML** plus die passende Trace-/Substratverlängerung, sonst schließt
    openEMS den Port an der PEC-Wand kurz. Die Portebene bleibt am PCB-Rand.
- Der **Mesh Viewer** zeigt die resultierende FDTD-Domain als schwarz
  gestrichelten Rahmen; im Untertab *Domain overview* steht die Größe.

### Materialien

Unterstützte `kind`-Werte:

| `kind` | Bedeutung |
|---|---|
| `pec` | Perfect Electric Conductor |
| `dielectric` | normales Dielektrikum |
| `conducting_sheet` | dünne leitfähige Schicht / Lossy Metal |
| `lumped_rlc` | konzentriertes RLC-Material |
| `ignore` | wird beim Import ignoriert |

CST-Typen werden im Makro gemappt: `PEC → pec`, `Lossy Metal`/`Ohmic sheet →
conducting_sheet`, `Normal → dielectric`. Nicht exakt abbildbare Typen werden
als Dielektrikum exportiert und in `warnings` gemeldet.

### Objekte

`objects` kann eine Liste oder eine Zuordnung `Name → Eintrag` sein. `file` ist
relativ zum Manifest (oder absolut). Unterstützt werden `.stl` und `.obj`; ein
anderes Format wird mit Warnung übersprungen. `kind` ist `solid` oder `sheet`
(Flächen werden als Polyhedron importiert).

### Ports

Unterstützt: `lumped`, `msl`, `rect_waveguide` sowie `waveguide` als Alias für
`rect_waveguide`. `probe` wird als Porttyp übernommen, aber nicht von jedem
Solverpfad unterstützt. Nicht unterstützt: Floquet-/Unit-Cell-Ports (Warnung).

`start`/`stop` sind Pflicht (oder werden aus `object`-Bounds abgeleitet).
`direction` ist bei Bedarf die kürzeste Boxachse; für `rect_waveguide` wird
`wg_a`/`wg_b` aus der Portbox abgeleitet, wenn nicht angegeben.

**Planare CST-Waveguide-Ports:** CST nennt auch Mikrostreifen-Feeds
„Waveguide Port“. Liegt die Portebene auf einem Dielektrikum mit einer dünnen
PEC-Leiterbahn, erkennt der Import das automatisch und erzeugt statt eines
Rechteck-Hohlleiterports einen **MSL-Port** (Leiterbahn = Signal,
Substrat-Gegenfläche = Ground, Ausbreitung entlang der Portnormale). Der
Importdialog zeigt zunächst den CST-Typ; nach dem Import steht die Umwandlung
in der Warnungsliste. Liegt ein solcher MSL-Port exakt auf einer PEC-/PMC-Randfläche
der Rechenbox (typisch bei der CST-Rechenbox mit Randabstand 0), erzeugt der
Import außerhalb der Portebene eine kurze **Trace-Verlängerung (1×
Tracebreite)** und verlängert das Substrat um **zwei Tracebreiten** ab der
Portebene. Diese Kante wird auf **PML** gesetzt.
CST behandelt die Fläche als Wellenleiter-Port; openEMS würde sie sonst als
geschlossene Metallwand oder als offenes Leitungsende interpretieren. Die
Portebene selbst bleibt an der CST-PCB-Kante. Die übrigen PEC-Wände,
insbesondere zmin als Ground und zmax als Deckel, bleiben unverändert. Echte
Hohlleiterports bleiben `rect_waveguide`; eine echte Nulldicke in
Ausbreitungsrichtung wird für openEMS um eine kleine Länge nach innen ergänzt.

Die dünne Kupferoberseite (z. B. 0,01778 mm) wird beim Aufräumen des Gitters als
**authored geometry** geschützt. Ohne diesen Schutz wurde die obere
Leiterbahn-Gitterlinie als „zu kleine Zelle“ entfernt; der MSL-Streifen lag dann
zwischen zwei Gitterlinien und openEMS meldete ihn als `Unused primitive`. Das
äußert sich in praktisch verschwindendem S21 und ist kein gültiges
Filterergebnis.

### Randbedingungen

`electric`/`tangential → PEC`, `magnetic`/`normal → PMC`,
`open`/`expanded open → MUR`, `pml → PML`. `periodic`, `unit cell` und
unbekannte Werte werden als Warnung gemeldet; die Projekt-Randbedingung bleibt
für diese Seite unverändert. Fehlt `pml_size`, bleibt die PML-Größe des
Projekts erhalten.

## Importoptionen

Der Dialog steuert:

- Materialien übernehmen,
- Ports übernehmen,
- Frequenzbereich übernehmen,
- Randbedingungen übernehmen,
- Flächen/Sheets importieren,
- Y spiegeln (nur für gespiegelte Modelle),
- Ziel-Einheit (aus dem Projekt, nur Anzeige).

Die Optionen werden mit dem Projekt in `CstSpec` gespeichert. Ein erneuter
Import aus demselben Paket ersetzt nur die Primitive/Ports dieses Pakets;
manuelle Material-/Prioritätsänderungen an den Primitiven bleiben erhalten.

## Nicht unterstützt / Warnungen

- natives `.cst` (proprietärer Container),
- CST-eigene Mesh-Einstellungen (VisualopenEMS baut sein eigenes Mesh),
- Floquet-/Unit-Cell-Ports,
- dispersive/anisotrope Materialmodelle (Werte werden als isotropes
  Dielektrikum übernommen),
- Schematic-, Feldmonitor- und Solver-Ergebnisdaten,
- Verlusttangente aus CST (siehe oben).

## Tests

`tests/test_cst_import.py` erzeugt ein Paket mit zwei STL-Boxen und prüft
Ordner/ZIP, Meter→mm/µm, Materialien, Ports, Frequenz, Ränder, Re-Import und
Projekt-Persistenz.
