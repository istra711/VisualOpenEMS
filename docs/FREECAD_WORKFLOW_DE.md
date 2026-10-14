# Anleitung: FreeCAD → Geometry → Ports → Import → Simulation

> English version: **[FREECAD_WORKFLOW_EN.md](FREECAD_WORKFLOW_EN.md)**

**Ziel:** kompletter Workflow von der Zeichnung in FreeCAD bis zur laufenden
openEMS-Simulation in der GUI — Schritt für Schritt, mit Häkchenliste.

**Voraussetzungen**

| Was | Wert |
|-----|------|
| FreeCAD | 1.1.4 (`c:\FreeCAD_1.1.4-Windows-x86_64\`) |
| FreeCADCmd | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe` |
| Python | `c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe` |
| Einheiten | **View → Units → mm** (Overlays: mm) |
| Dokumenttyp | **FCStd** speichern (trägt Ports; STEP/STL verlieren sie) |
| GUI-Import | **Import Geometry…** oder **FreeCAD… → Export CAD…** |

**Warum FCStd?** Nur FreeCAD-native Formate (`.fcstd`) und die
`openems_export.json`-Manifeste der FreeCAD-Tools tragen die Port-Markierungen
durch den Export. Ein reines STEP/STL ist reine Geometrie **ohne** Ports.

---

## Überblick (der 7 Schritte)

| # | Schritt | Wo |
|---|---------|----|
| 1 | Einheiten + Orientierung | FreeCAD |
| 2 | Geometrie zeichnen (ein Solide = ein Objekt) | FreeCAD |
| 3 | Ports markieren (`PORT_<name>_start` / `_stop`) | FreeCAD |
| 4 | Als FCStd speichern | FreeCAD |
| 5 | In die GUI importieren | GUI |
| 6 | Mesh bauen | GUI (**Strg+M**) |
| 7 | Simulation starten + auswerten | GUI (**Strg+R**) |

---

## 1. Einheiten und Orientierung

- **View → Units → mm**.
- Freie Ausrichtung, aber **konsistent**; GUI erwartet mm (Default `drawing_unit=mm`).
- Übliche Antennen-Orientierung: **Z = Höhe**, Patch/Dipol in XY-Ebene.

Empfehlung: **Ein Solide = ein Objekt**, mit klarem Namen (geht später als
Objektname ins Projekt, z. B. `gnd`, `substrate`, `patch`).

---

## 2. Geometrie zeichnen (FreeCAD)

### 2.1 Patch-Antenne 2,45 GHz (FR4, εr≈4,4, h=1,6 mm)

| Bauteil | Maße (mm) | FreeCAD-Tipp |
|---------|-----------|--------------|
| Substrat | 60 × 60 × 1,6 | Box, z = 0…1,6 |
| Ground (GND) | 60 × 60 × 1 | Box, z = −1…0 |
| Patch | ~36 × 28 × 1 | Box auf Substrat, z = 1,6…2,6 |
| Feed-Stelle | 5–7 mm vom Patchrand | spätere Port-Definition |

**Namen im Object Tree:** `gnd`, `substrate`, `patch`.

### 2.2 Draht-/Yagi-Struktur (alternativ)

- FreeCAD-Zylinder/Part-Tools oder **Preset: Yagi Antenna…** in der GUI.
- Koordinaten-System Yagi: **X = Elemente, Y = Boom, Z = oben**.
- Treiber-Dipol mit Lücke → dort sitzt der Port.

---

## 3. Ports in FreeCAD markieren (empfohlen)

Jeder Port braucht **zwei Punkte** (Variante A, empfohlen):

| Label (Name) | Bedeutung |
|--------------|-----------|
| `PORT_<name>_start` | Start des Port-Vektors |
| `PORT_<name>_stop`  | Ende des Port-Vektors |

**Namensschema:** `PORT_` + Portname + `_start` / `_stop`
(Beispiele: `PORT_in1_start`, `PORT_in1_stop`, `PORT_probe_start`, `PORT_probe_stop`).

**So in FreeCAD:**

1. Menü **Part → Vertex** (oder Draft Point).
2. Im Tree **umtaufen / Label** setzen: exakt `PORT_in1_start` bzw. `PORT_in1_stop`.
3. Punkte an die **Feed-Position** setzen (Placement oder Task-Bearbeitung).
4. **File → Save** als `.FCStd`.

> Wichtig: **Label** (nicht nur interner Name) muss dem Schema entsprechen —
> beide werden gelesen.

**Wo sitzt der Port? (Patch 2,45 GHz):**

```text
start = (feed_x, feed_y, z_gnd)     # z. B. (-6.0, 0.0, -1.0)
stop  = (feed_x, feed_y, z_patch)   # z. B. (-6.0, 0.0,  1.6)
```

- Vektor **senkrecht** zwischen Patch und Ground (typisch **+Z** / −Z).
- Start/Stop müssen **deutlich verschieden** sein (nicht identische Punkte).
- Richtung wird im Programm aus dem größten Δ abgeleitet (`x`/`y`/`z`).

**Yagi / Dipol (Lücke):**

```text
start = (gap_x_left,  y0,  z0)
stop  = (gap_x_right, y0,  z0)
```

**Weitere Ports:** pro Port ein weiteres Paar, z. B.
`PORT_in1_*` (Anregung, excite = true) + `PORT_probe_*` (Messung, excite = false).

PORT-Punkte werden **nicht** als Meshes tesselliert (nur als Marker gelesen).

> Details zu allen drei Varianten (Punkte / Custom Properties / Dokument-JSON),
> Fehler-Tabelle und Beispiele: **[FREECAD_PORTS_DE.md](FREECAD_PORTS_DE.md)**.

---

## 4. Als FCStd speichern

1. **File → Save As…** → `*.FCStd`
2. Pfad, z. B.
   `C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export\antenne.FCStd`
3. Optional zusätzlich: **File → Export…** → **STEP** (`*.step`) — reine Geometrie
   für andere Werkzeuge, aber **ohne** Ports.

---

## 5. In die GUI importieren

GUI starten:

```text
C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
python app\main.py
```

**Variante A — Import (Standard):**

1. **Import Geometry…** (File-Menü)
2. Datei wählen: `.fcstd` (bevorzugt, trägt Ports) oder `.step`/`.stp`/`.stl`/`.obj`/`openems_export.json`
   (auch ein Ordner mit Objekt-STLs, z. B. `stl/`, ist möglich)
3. FreeCADCmd tesselliert STEP/FCStd → `.npy`-Meshes; STL/Manifest werden direkt
   geladen — **ein Primitiv pro FreeCAD-Objekt**, Objektnamen bleiben erhalten
4. Objekte erscheinen als **stl-Primitives** (Default Material: copper / PEC)
5. Log prüfen: Mesh-Pfade, Port-Hinweis `Ports auto-added`, ggf. WARNINGS

**Variante B — FreeCAD-Tools in der GUI:**

1. **FreeCAD…** → **Launch FreeCAD** (öffnet FreeCAD, cwd = Work-Dir) — zeichnen/ändern
2. **Export CAD…**
   - Quelle: `.fcstd` (bevorzugt) oder `.step`
   - **Import as:** `STEP` (tesselliert die App später) oder
     `STL (one mesh per FreeCAD object)` (nutzt `openems_export.json`,
     kein FreeCAD zur Sim-Zeit nötig)
   - Deflection: 0,1 mm (Standard)
   - Häkchen **Import meshes into project after export**
3. OK → Export → Auto-Import ins Projekt

### Ports prüfen

1. Log lesen: Hinweis zu `Ports auto-added`.
2. **Geometry → Ports / Dumps…** (oder **Strg+Umschalt+D**) öffnet das
   **Ports / Dumps**-Fenster.
3. Port(s) erscheinen in der Liste (z. B. `in1`, R = 50, excite an).
4. Optional: Doppelklick zum Editieren, **Edit Port…** / **Remove Port**.
   Änderungen greifen sofort (kein Apply nötig — das Fenster hostet die Live-Widgets).

Fehlstart/identische Punkte → Port wird übersprungen (Log-Warnung möglich).

> Schritt-für-Schritt-Details, Custom Properties, Dokument-JSON und
> Fehler-Tabelle: **[FREECAD_PORTS_DE.md](FREECAD_PORTS_DE.md)**.

---

## 6. Mesh bauen

1. **Mesh → Mesh Parameters…** (oder **Strg+Umschalt+M**) — Preset, f0/fc,
   start/stop, Drawing unit, Readout; **Apply** übernimmt ins Projekt.
2. **Mesh → Build CSX + Mesh + Validate** (oder **Strg+M**) — tesselliert und prüft.
3. Ergebnis im **Log-Panel → Mesh Report**-Tab (einzige Quelle nach Build).
4. **View → Mesh Viewer** zeigt die Szene; **Slice slider** snappt auf Mesh-Linien.

---

## 7. Simulation starten und auswerten

1. **Simulation → Simulation Parameters…** (oder **Strg+Umschalt+P**) —
   end_criteria, max_timesteps, Boundary ×6 + PML, Air margin, NF2FF.
   **Apply** schreibt ins Projekt.
2. Optional: **Geometry → Fit NF2FF** (Strahlungsdiagramm-Box an Geometrie anpassen).
3. **Simulation → Run Simulation** (oder **Strg+R**).
4. **Log**-Tab beobachten (openEMS-Lauf).
5. Ergebnis: **Simulation → S-Params Viewer** (S-Matrix / S11) bzw.
   NF2FF-Plot (Strahlungsdiagramm).

---

## Checkliste (gesamt)

- [ ] FreeCAD: **View → Units → mm**
- [ ] Ein Solide = ein Objekt, klar benannt (`gnd`, `substrate`, `patch`)
- [ ] Feed-Punkte mit Label `PORT_in1_start` / `PORT_in1_stop`
- [ ] Vektor spannt die Port-Öffnung (Patch ↔ GND oder Dipol-Lücke)
- [ ] **FCStd** gespeichert (nicht nur STEP)
- [ ] GUI: **Import Geometry…** (`.fcstd`) → Log: `Ports auto-added`
- [ ] **Geometry → Ports / Dumps…** prüfen (50 Ω, Excite an Port 1)
- [ ] **Mesh Parameters…** → **Build CSX + Mesh + Validate (Strg+M)** → Mesh Report prüfen
- [ ] **Simulation Parameters…** → **Run Simulation (Ctrl+R)**
- [ ] S-Params / NF2FF auswerten

---

## Häufige Fehler

| Problem | Lösung |
|---------|--------|
| Port fehlt nach Import | Label/Name prüfen: `PORT_in1_start` + `_stop`; **FCStd** statt STEP |
| Nur `_start` vorhanden | Beide Punkte nötig, gleiche Namensbasis |
| Identische Punkte | Start ≠ Stop, Vektor ≠ 0 |
| Port am falschen Ort | Punkte auf Feed-Lücke / Patch–GND |
| STEP-Import ohne Ports | **FCStd** verwenden (Custom Props/JSON gehen bei purem STEP oft verloren) |
| Port nicht excitiert | **Ports / Dumps…** → Excite an Port 1 |
| Mesh fehlt am Port | Mesh Parameters: höheres Preset; Ports **vor** Build prüfen |
| Domain zu groß/klein | **Simulation Parameters…** → Air margin; danach Build + Fit NF2FF |

---

## Verwandte Dateien

| Datei | Zweck |
|-------|--------|
| `docs\FREECAD_PORTS_DE.md` | Port-Markierung im Detail (3 Varianten) |
| `docs\ANLEITUNG_DE.md` | Kompletter Workflow + Troubleshooting |
| `app\freecad_io\port_marks.py` | Extraktion FreeCAD → PortDef |
| `app\freecad_io\freecad_mesh.py` | FCStd/STEP → STL/npy tessellieren |
| `app\freecad_io\importer.py` | Geometrie-Import |
| `examples\freecad_dipole_ports.py` | Dipol-Beispiel mit PORT-Markern |
| `examples\run_dipole_ports_example.py` | Dipol generieren + Ports verifizieren |
| `examples\freecad_add_port_marks.py` | Demo: Port-Punkte anlegen |
| `tests\dipole_ports_smoke.py` | Smoke Dipol-Ports |
| `app\ui\tab_help.py` | GUI-Hilfe Tabs / Fenster |
