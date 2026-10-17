# Anleitung: Antenne in FreeCAD zeichnen und mit der GUI simulieren

> English version: **[ANLEITUNG_EN.md](ANLEITUNG_EN.md)**

**Projekt:** FreeCAD OpenEMS GUI  
**Ziel:** 3D-Antenne in FreeCAD modellieren, als STEP importieren, mit openEMS simulieren und S-Params auswerten  
**Sprache der GUI:** Englisch (Anleitung: Deutsch)

---

## 1. Überblick

| Schritt | Werkzeug | Ergebnis |
|--------|----------|----------|
| Antenne zeichnen | FreeCAD | FCStd / STEP |
| Export | FreeCAD → Export STEP | `.step` |
| Import | GUI **Import Geometry…** oder **FreeCAD…** | Triangle-Meshes + stl-Primitives |
| Anbindung | **Add Port…**, **Add Assignment…** | 50 Ω Port, Material |
| Simulation | **Build Mesh** → **Run Simulation** | S-Params, Field |
| Auswertung | **S-Params Viewer**; **Antenna**-Tab | S11/S21, Smith, Marker; Muster (Gain/Direktivität) |

**Wichtig:** openEMS liest BREP-STEP nicht direkt. Die GUI tesselliert CAD automatisch zu Dreiecks-Meshes (`CSX.AddMesh`).

**Kompakte Schritt-für-Schritt-Anleitung (FreeCAD → Geometry → Ports → Import → Simulation):**
**[docs/FREECAD_WORKFLOW_DE.md](FREECAD_WORKFLOW_DE.md)** — mit Checkliste und Fehler-Tabelle.
Details nur zur Port-Markierung: **[docs/FREECAD_PORTS_DE.md](FREECAD_PORTS_DE.md)**.

---

## 2. FreeCAD: Antenne zeichnen

Empfehlung: **Ein Solide = ein Objekt**, mit klarer Bezeichnung (geht später als Name ins Projekt).

### 2.1 Einheiten

- FreeCAD: **View → Units** → **mm**
- Objekte in mm zeichnen (GUI-Default `drawing_unit=mm`)

### 2.2 Konkretes Beispiel: Patch-Antenne 2,45 GHz (FR4, εr≈4,4, h=1,6 mm)

Orientierung: **Z = Höhe**, Patch in XY-Ebene.

| Bauteil | Maße (mm) | FreeCAD-Tipp |
|---------|-----------|--------------|
| Substrat | 60 × 60 × 1,6 | Box, Position z = 0…1,6 |
| Ground (GND) | 60 × 60 × 1 | Box, z = −1…0 |
| Patch | ~36 × 28 × 1 | Box auf Substrat, z = 1,6…2,6 |
| Feed-Stelle | 5–7 mm vom Patchrand | spätere Port-Definition |

**Namen im Modell** (Object Tree): `gnd`, `substrate`, `patch`.

**Port (lumped):**  
Lumped-Port an einer Kante des Patches: start/stop am Patch, 50 Ω, Richtung `x` oder `y` (später in der GUI).

**Ports schon in FreeCAD markieren (empfohlen):**  
Punkte `PORT_in1_start` / `PORT_in1_stop` anlegen → Import fügt den Port automatisch hinzu.  
Schritt-für-Schritt: **[docs/FREECAD_PORTS_DE.md](FREECAD_PORTS_DE.md)**

### 2.3 Draht-/Yagi-Struktur (alternativ)

- **Preset: Yagi Antenna…** in der GUI (referenced, 2,45 GHz) oder FreeCAD-Zylinder/Part-Tools
- Koordinaten-System Yagi: **X = Elemente, Y = Boom, Z = oben**
- Treiber-Dipol mit Lücke → dort Port

### 2.4 FreeCAD-Export

1. **File → Export…**
2. Typ: **STEP** (`*.step` / `*.stp`) — empfohlen
3. Speichern unter z. B.  
   `C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export\antenne.step`
4. Optional zusätzlich: **FCStd** speichern oder **STL** exportieren

---

## 3. GUI starten

```text
C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
python app\main.py
```

**Menüleiste:** `File` (New/Open/Save, Import Geometry, Open Work Folder, Exit),
`Project` (Presets), `Geometry` (Box/Cylinder/Port/Dump, Ports / Dumps… = **Strg+Umschalt+D**, Edit/Duplicate/Delete Selected = **Strg+D**/**Entf**, Fit NF2FF — das Gleiche per Rechtsklick auf einen Tree-Eintrag),
`Mesh` (`Build CSX + Mesh + Validate` = **Strg+M**), `Simulation` (`Run Simulation` = **Ctrl+R**, Script,
S-Params Viewer, Antenna Pattern Viewer / Pop-out Antenna Graph),
`View` (Tabs, Log Panel = **Ctrl+L**), `Tools` (Settings…),
`Help` (Help = **F1**, ANLEITUNG, README, About).

Das **Log-Panel** (unterhalb der Tabs, ein-/ausblendbar mit **Ctrl+L**) hat zwei
Tabs: **Log** (openEMS-/App-Log) und **Mesh Report** (der Mesh-Check-Report,
einzige Quelle nach Build).

**Settings…** (einmalig):

| Feld | Wert |
|------|------|
| FreeCADCmd | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe` |
| FreeCAD GUI | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCAD.exe` |
| Simulation Python | `c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe` |
| openEMS root | `c:\openEMS` |
| Work dir | `C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work` |

---

## 4. Modell in die GUI bringen

### Variante A — Import (Standard)

1. **Import Geometry…**
2. Datei wählen: `.step` / `.stp` / `.fcstd` / `.stl` / `.obj` / `openems_export.json`
   (auch ein Ordner mit Objekt-STLs, z. B. `stl/`, ist möglich)
3. FreeCADCmd tesselliert STEP/FCStd → `.npy`-Meshes; STL/Manifest werden direkt
   geladen — **ein Primitiv pro FreeCAD-Objekt**, Objektnamen bleiben erhalten
4. Objekte erscheinen als **stl-Primitives** (Default Material: copper / PEC)
5. Log prüfen: Mesh-Pfade, ggf. WARNINGS

### Variante B — FreeCAD-Tools in der GUI

1. **FreeCAD…**
2. **Launch FreeCAD** (öffnet FreeCAD, cwd = Work-Dir) — zeichnen/ändern, STEP exportieren
3. **Export CAD…**
   - Quelle: `.fcstd` (bevorzugt — Objektnamen bleiben erhalten) oder `.step`
   - **Import as:** `STEP` (tesselliert die App später) oder
     `STL (one mesh per FreeCAD object)` (nutzt `openems_export.json`, kein FreeCAD zur Sim-Zeit nötig)
   - Deflection: 0,1 mm (Standard)
   - Häkchen **Import meshes into project after export**
4. OK → Export → Auto-Import ins Projekt

Headless (ohne GUI):

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_stl_import_example.py
```

### Variante C — Preset (nur zum Testen der Simulation)

1. **Preset: Patch Antenna…**, **Preset: Yagi Antenna…** oder **Preset: PCB Filter…**
2. **PCB Filter** = 2-Port-Mikrostreifen-Notchfilter auf FR4 (Stopband bei 2,45 GHz):
   zwei Lumped-Ports, damit misst das Beispiel beide S-Matrix-Spalten.
   Die Geometrie kommt aus **FreeCAD** (FCStd → STL, 35 µm Kupfer); `--analytic`
   nutzt alternativ die analytischen Box-Primitives zum Vergleich

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_pcb_filter_example.py
```

Ergebnis (`work\pcb_filter\simulation\`): FreeCAD-Lauf **−29,99 dB bei 2,450 GHz**
Zielwert, Durchlass −0,72 dB bei 0,3 GHz; analytischer Lauf −28,96 dB bei 2,429 GHz
(0,9 % Unterschied = echte 35 µm Kupferschicht vs. null dicke Leiterbahn im Tutorial).

Hinweise:

- **Achsenparallele** FreeCAD-Körper werden als openEMS-**Box** angelegt (exakte
  Metallkanten auf den Gitterlinien), alles andere bleibt STL-`PolyhedronReader`.
  In-Memory-`Polyhedron`-Primitive simuliert openEMS **nicht** (Warnung
  *Unused primitive*).
- `[WARNING] grading_jump: Axis z 9.14` ist normal: die 35 µm Kupfer erzwingen
  0,035-mm-Zellen neben den 0,32-mm-Substratzellen — nur eine Warnung, Exit-Code 0.
- `Max. number of timesteps was reached` ist hier erwartet; das Stopband ist stabil
  (0,019 dB Änderung von 50 000 auf 150 000 Schritte, `work\pcb_filter_conv`).

---

## 5. Projekt konfigurieren

### 5.0 Simulations-Parameter (eigenes Fenster)

**Menü → Simulation → Simulation Parameters…** (oder **Strg+Umschalt+P**) öffnet
ein eigenes, verschiebbares Fenster mit **allen** Simulationsparametern —
zwei Tabs:

| Tab | Inhalt |
|-----|--------|
| **Simulation-Parameter** | Computed (read-only), Excitation & convergence (End criteria, NrTS), Boundary conditions ×6 + PML, Air margin, Mesh-Overrides, Patch-Geometrie |
| **NF2FF-Parameter** | Enable far-field (NF2FF), **Evaluation frequencies** (Mode: *Auto (S11 minimum)* / *N points (mesh range)* / *Frequency list*), θ/φ-Steps, Fit/Edit NF2FF-Box |

**Evaluation frequencies** (NF2FF-Tab) legen fest, an wie vielen Frequenzen
das Fernfeld nachgerechnet wird:

| Mode | Wirkung |
|------|---------|
| **Auto (S11 minimum)** | eine Frequenz am S11-Tal (bzw. Ziel-Frequenz) |
| **N points (mesh range)** | N Punkte gleichmäßig über den Mesh-Frequenzbereich (start/stop vom Mesh-Tab) — z. B. 11 Punkte von 2,0…2,8 GHz → Kurve *Gain vs Frequency* im Antenna-Tab |
| **Frequency list** | explizite Liste, jeder Eintrag mit Einheit **Hz / kHz / MHz / GHz** ( nackte Zahl = Hz), z. B. `2.4 GHz, 2.45 GHz, 2500 MHz` |

| Knopf | Wirkung |
|-------|---------|
| **Apply** | Schreibt die Werte ins Projekt, spiegelt sie in das (versteckte) Sim-Params-Panel (Speichern/Build nutzen es) und aktualisiert `work/<Projekt>/project_run.py`, falls es schon existiert (ein offenes Script-Editor-Fenster lädt die Datei neu, solange dort keine ungesicherten Änderungen sind) |
| **Close** | Verwirft nicht angewandte Änderungen |

Das Fenster ist **modeless** — ein zweiter Menüklick bringt dasselbe Fenster
nach vorn und synchronisiert es mit dem aktuellen Projekt. Änderungen wirken
erst nach **Apply**; danach **Build Mesh**, damit das Netz sie übernimmt.

**Menü → Mesh → Mesh Parameters…** (oder **Strg+Umschalt+M**) öffnet ein
eigenes, verschiebbares Fenster mit **allen** Mesh-Parametern (preset,
Zeicheneinheit, f0/fc, start/stop, Air margin). **Apply** schreibt sie ins
Projekt und spiegelt sie in die Mesh-Form, **Close** verwirft nicht
angewandte Änderungen. Das Fenster ist ebenfalls **modeless**.

### 5.1 Mesh / Frequenzen

Mesh-Parameter (siehe §5.0):

| Parameter | Patch 2,45 GHz | Yagi 2,45 GHz |
|-----------|----------------|---------------|
| start_frequency | 2.2e9 | 2.2e9 |
| stop_frequency | 2.7e9 | 2.7e9 |
| target_frequency | 2.45e9 | 2.45e9 |
| drawing_unit | mm | mm |
| max_timesteps | 150000+ | 25000 (Preset) |

### 5.2 Material / Assignment

1. **Add Material…** — z. B. `substrate` (εr=4,4, tanδ=0,02), `copper`
2. **Add Assignment…**
   - Object: `substrate` (oder Substrat-Name aus Import)
   - Role: `dielectric` / `pec` / `conductor` …
   - Material wählen
3. Assignment synchronisiert stl-Primitives (pec → copper, dielectric → substrate …)

### 5.3 Port

1. **Add Port…**
2. Typ: **lumped** (Patch) — Resistanz **50**
3. start / stop: Kante des Patches (mm), z. B.  
   `start=[-1.0, 0.0, -1.0]`, `stop=[1.0, 0.0, 1.0]`
4. Direction: `x` oder `y` (parallel zum Spannungsabfall am Port)
5. `excite_port = true` (Port 1)
6. Geometrie prüfen: Viewer / Mesh-Preview

**Alternativ (FreeCAD):** Ports als `PORT_*`-Punkte im FCStd markieren — dann erscheinen sie beim Import automatisch.  
→ **[docs/FREECAD_PORTS_DE.md](FREECAD_PORTS_DE.md)**

### 5.4 NF2FF (Strahlungsdiagramm)

1. **Edit NF2FF Box…** oder **Fit NF2FF to Geometry**
2. Box um Antenne, z. B. Start `[-40,-40,-20]`, Stop `[40,80,20]`, Center `[0,20,0]`
3. `enabled = true`
4. **Evaluation frequencies** wählen (Sim Params → NF2FF-Tab, siehe §5.0):
   *Auto* (eine Frequenz am S11-Minimum), *N points* (Kurve Gain vs Frequency)
   oder eine explizite Liste mit Einheiten (z. B. `2.4 GHz, 2.45 GHz`).
5. Der **Mesh Viewer** zeigt die neue Box sofort an (violette strich-punkt Linie) —
   vorausgesetzt, das Mesh wurde bereits gebaut; die schwarze gestrichelte Linie ist
   die Simulationsdomain und ändert sich erst beim nächsten **Build Mesh**.

### 5.5 Projekt speichern

**Save…** → z. B. `work\meine_antenne\antenne.fcsim.json`

### 5.6 Domain-Größe (schwarzer Rahmen im Mesh Viewer)

Die Domain lässt sich nicht direkt bearbeiten — sie ergibt sich aus **Geometrie +
Air-Margin + Gitterrundung**. Hebel:

| Hebel | Wirkung |
|-------|---------|
| **Air margin auto** (Sim Params → Boundary conditions) | λ/4 bei **Stop-Frequenz**, immer εr=1 (bei 2,7 GHz = 27,76 mm pro Fläche). Wer die Stop-Frequenz ändert, ändert damit ebenfalls das Margin |
| **Air margin (all faces)** | Auto aus → fester Wert in mm für alle 6 Flächen. In der Projekt-JSON als 6er-Liste `boundary.air_margin` = `[xmin,xmax,ymin,ymax,zmin,zmax]` pro Fläche einstellbar (die GUI schreibt allen denselben Wert) |
| **Geometrie** | Domain = Geometrie-BBox + Margin (easyMesh `boundary_distance`) |
| **PML size** | pro PML-Fläche hängt easyMesh `PML_size + 1` Zellen **außerhalb** an → Domain größer. Wirkung nur bei Boundary = PML; bei MUR ohne Effekt |
| **Mesh-Preset / min cell size** | Luftzellen wachsen bis λ/10…λ/40 je Preset (`max_cellsize_air`) → die äußere Gitterlinie wird darauf gerundet: Abweichung um wenige mm, pro Seite leicht unterschiedlich (das ist die kleine Asymmetrie der schwarzen Linie) |

**Ablauf:** Werte ändern → **Build Mesh** (schwarze Linie aktualisiert sich) →
**Fit NF2FF to Geometry**, damit die violette Box innerhalb bleibt. Das Ergebnis
steht im Info-Panel des Viewers als *Domain size*.

**Grenzen:** zu klein → Warnung `outside the field domain`, NF2FF/Dumps werden
geklemmt oder deaktiviert; zu groß → deutlich mehr Zellen (RAM/Laufzeit).
Geometrie, die über die schwarze Linie hinausragt, meldet der Validator als
`primitive_outside_domain` (**error**, blockiert den Lauf; Dumps/Probes nur als
Warning) — der Mesh Viewer umrahmt solche Primitive rot und schreibt
*OUTSIDE DOMAIN* in die Übersicht.

---

## 6. Simulieren

1. **Build Mesh**
   - Prüfen: Mesh-Zellen, Ports, Preview (`mesh_preview.png`)
   - Viewer zeigt 3D-Geometrie
2. **Run Simulation**
   - Log-Panel: openEMS läuft, max_timesteps, end_criteria
   - Warnung bei `max_timesteps < 150000` möglich
3. **Script…** (Simulation-Menü) — `project_run.py` ansehen und bearbeiten
   - Das Skript ist das, was openEMS wirklich ausführt (Pfade, Mesh, Ports, Export)
   - **Save** / **Revert** / **Regenerate from project**
   - **Run Simulation** erzeugt das Skript neu; eigene Änderungen werden vorher nach
     `project_run_edited.py` gesichert — die GUI fragt, ob neu erzeugt oder dein
     bearbeitetes Skript so wie es ist ausgeführt werden soll
   - Manuell: `<python> work\<Projektname>\project_run.py`
4. Ergebnis-Dateien unter `work\<Projektname>\` (u. a. `project_run.py`, Meshes, S-Params)

---

## 7. Auswertung

1. **S-Params Viewer**
   - S11 / S21, dB, Phase
   - Marker: Frequenz, S, Γ, Z/Y, VSWR, RL
   - Smith-Chart: Z- und Y-Marker
2. **Pop-out S-Graph** — Plot in Extra-Fenster
3. **Antenna**-Tab / **Simulation → Antenna Pattern Viewer** — NF2FF-Fernfeld:
   3D-Polar des Gewinns, 2D-Polarschnitte (E/H-Ebene), Direktivität/Gain/η
   über Frequenz; **Pop-out Antenna Graph** = Extra-Fenster; Feld-Dumps
   weiterhin unter `work/<projekt>/fields/` (S-Parameter in
   `work/<projekt>/sparams/`, NF2FF in `work/<projekt>/nf2ff/`)
4. **2-Port (S2P)** — ein FDTD-Lauf misst **eine** Spalte der S-Matrix (für 2 Ports:
   S11 + S21). Weitere Spalten brauchen weitere Läufe mit anderem erregtem Port
   (`excite_port` umschalten); die JSON-Ergebnisse werden danach zusammengeführt —
   Beispiel: `examples\run_pcb_filter_example.py`.
   Nicht gemessene Einträge bleiben leer (NaN) in `sparams.json`, die Touchstone-Datei
   (`.s2p`) entsteht erst, wenn die Matrix vollständig ist.

---

## 8. Workflow-Checkliste (Antenne 2,45 GHz)

- [ ] FreeCAD: `gnd`, `substrate`, `patch` in mm
- [ ] Ports in FreeCAD: `PORT_in1_start` / `PORT_in1_stop` (optional, empfohlen)
- [ ] Export: `.step` (oder `.fcstd` mit Ports)
- [ ] GUI: **New Project**
- [ ] Mesh: 2,2–2,7 GHz, target 2,45e9, unit mm
- [ ] **Import Geometry…** → STEP / FCStd
- [ ] **Add Assignment…** Substrat (dielectric) + copper
- [ ] **Add Port…** lumped 50 Ω am Feed (oder FreeCAD-Ports prüfen)
- [ ] **Fit NF2FF to Geometry** + enabled
- [ ] **Save…**
- [ ] **Build Mesh** → **Run Simulation**
- [ ] **S-Params Viewer** → S11-Minimum nahe 2,45 GHz

---

## 9. Häufige Probleme

| Problem | Ursache / Lösung |
|---------|------------------|
| Import macht nichts bei STEP | FreeCADCmd-Pfad in Settings prüfen |
| Port fehlt oder falsch | start/stop nicht auf Kante des Patches |
| S11 schlecht | Feed-Lage, Patchmaße, Mesh zu grob, max_timesteps zu niedrig |
| Material falsch | Assignment fehlt; Default ist copper/PEC |
| FreeCAD startet nicht | Settings **FreeCAD GUI** (`FreeCAD.exe` neben `FreeCADCmd`) |
| Export ohne STL | In FreeCAD exportieren / FreeCAD… → Export CAD… |
| Sim bricht ab / leere S-Params | Mesh/Port prüfen; Log-Pfad `work\<Projekt>\` |
| `AssertionError` in `openEMS.Run` | `project_run.py` hat relative Pfade (`WORK_DIR=…`) → Skript neu erzeugen (Pfade werden absolut gespeichert) |
| Warnung `outside the field domain` | Dump/NF2FF-Box zu klein → **Fit NF2FF to Geometry**; das Skript warnt und klemmt die Box in den FDTD-Bereich |
| Domain (schwarzer Rahmen) zu groß/klein | **Air margin** in Sim Params → Boundary conditions (auto = λ/4 bei Stop-Frequenz, sonst mm-Wert für alle Flächen) → danach **Build Mesh** + **Fit NF2FF** (siehe 5.6) |
| S11 flach bei ≈ 0 dB, Prad ≈ 0 | Port kurzgeschlossen — bei der Yagi der Boom: Preset setzt `gfrp` (Dielektrikum, Assignment `boom`), nicht `copper` |
| Eigenes Skript nach dem Run „weg" | `project_run.py` wird bei jedem Run neu erzeugt — deine Fassung liegt in `project_run_edited.py`; die GUI fragt vorher (neu erzeugen oder Skript so ausführen) |
| Bei 2-Port fehlen S12/S22 | ein Lauf = eine Spalte → zweiter Lauf mit anderem `excite_port`, danach zusammenführen (`examples\run_pcb_filter_example.py`) |

---

## 10. Tipps

- **Preset zuerst:** Patch/Yagi-Preset testen, dann eigene FreeCAD-Geometrie importieren.
- **Deutliche Objektnamen** in FreeCAD (`patch`, `gnd`, `via`) — erscheinen als Primitive-Namen.
- **STEP-Import:** Tessellation-Deflection ~0,1 mm; für feine Pins kleiner wählen.
- **FreeCAD-Export-Helper (CLI):**  
  `examples\freecad_export_for_openems.py` (FreeCADCmd mit `FREECAD_SOURCE` / `FREECAD_OUT_DIR`)
- Nach Materialänderung **Build Mesh** erneut ausführen.
- **Skript-Änderungen über „Script…"** machen — dann sichert der nächste Run deine
  Fassung automatisch nach `project_run_edited.py` und fragt, ob neu erzeugt oder so
  ausgeführt werden soll.
- **2-Port-Beispiel (Filter):** `Preset: PCB Filter…` in der GUI oder
  `examples\run_pcb_filter_example.py` — beide S-Matrix-Spalten, fertiges `sparams.s2p`.

---

## 11. Pfade & Tools (Referenz)

| Zweck | Pfad |
|-------|------|
| GUI starten | `app\main.py` |
| FreeCADCmd | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe` |
| FreeCAD GUI | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCAD.exe` |
| openEMS | `c:\openEMS` |
| Work-Verzeichnis | `work\` |
| Export-Skript | `examples\freecad_export_for_openems.py` |
| Port-Marker Demo | `examples\freecad_add_port_marks.py` |
| Dipol + Ports | `examples\freecad_dipole_ports.py`, `examples\run_dipole_ports_example.py` |
| FreeCAD-Port-Anleitung | `docs\FREECAD_PORTS_DE.md` |
| Yagi-Beispiel | `examples\freecad_yagi_model.py`, `work\yagi_antenna\` |
| Smoke-Tests | `tests\freecad_gui_smoke.py`, `tests\freecad_mesh_smoke.py`, `tests\port_marks_smoke.py`, `tests\dipole_ports_smoke.py` |
