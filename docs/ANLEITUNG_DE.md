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
`View` (Tabs, Log Panel = **Ctrl+L**, Project / Geometry Panel = **Ctrl+B**),
`Tools` (Settings…),
`Help` (Help = **F1**, ANLEITUNG, README, About).

Das **Projekt-/Geometrie-Panel** links (Tree) lässt sich per **Rechtsklick** auf
die leere Fläche darunter oder über `View` → **Project / Geometry Panel**
(**Ctrl+B**) aus- und wieder einblenden (Zustand wird gespeichert).

Das **Log-Panel** (unterhalb der Tabs, ein-/ausblendbar mit **Ctrl+L**) hat zwei
Tabs: **Log** (openEMS-/App-Log) und **Mesh Report** (der Mesh-Check-Report,
einzige Quelle nach Build). **Rechtsklick im gesamten Log-Bereich** (Text,
Tab-Leiste, Knopfzeile, Zeilenzähler oder der Trenner darüber) öffnet die
Log-Optionen: Log-Panel und Projekt-/Geometrie-Panel ein-/ausblenden, Log-Tab
wählen, Auto-scroll, Save log…, Pop out log…. Im Text stehen darüber zusätzlich
Copy / Select All. Das gleiche Menü liegt auf der Statuszeile ganz unten.

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

#### PCB-Bandpass 5,6 GHz (fünf Stubs, Teflon)

**Preset: PCB Bandpass (5.6 GHz)…** lädt einen 5-poligen Chebyshev-Bandpass
(0,1 dB Welligkeit) auf **PTFE/Teflon** (0,254 mm, εr = 2,1, tanδ = 0,0002,
35 µm Kupfer). Fünf **offene λ/2-Stubs** hängen an einer 50-Ω-Leitung, dazwischen
liegen vier **λ/4-Verbindungsleitungen** als Impedanz-Inverter — ohne Vias, also
einseitig planar.

| Element | Z (Ω) | Breite (mm) | Länge (mm) |
|---|---|---|---|
| Stub 1 / 5 (außen) | 50,8 | 0,80 | 18,37 |
| Stub 2 / 4 | 15,0 | 3,74 | 18,69 |
| Stub 3 (Mitte) | 68,0 | 0,50 | 22,91 |
| Linie 1 / 4 | 43,9 | 1,01 | 11,78 |
| Linie 2 / 3 | 61,0 | 0,60 | 8,04 |

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_pcb_bandpass_example.py
```

Ergebnis (`work\pcb_bandpass\sparams\`): **|S11| < −10 dB von 4,90 bis
6,45 GHz**, Durchlass **−0,1 … −0,3 dB**, Sperrdämpfung **−42 dB bei 4,0 GHz**
und **−38 dB bei 7,2 GHz** (2 FDTD-Läufe, je eine S-Matrix-Spalte, danach
zusammengeführt; Laufzeit ≈ 80 s pro Port auf 8 Kernen).

Hinweise:

- Das Gitter nutzt `min_cellsize = 0,15 mm` (schmalster Stub 0,50 mm) und
  `min_cellsize_z = 0,085 mm` (drei Zellen im 0,254-mm-Substrat).
- `smooth_metal_edge = False` und `handle_closely_placed_edges = False`:
  easyMesh erzeugte sonst zwischen 0,41 mm und 0,50 mm breiten Leiterkanten eine
  4-µm-Zelle, die den CFL-Zeitschritt um Faktor 8 verkleinert (Simulation lief
  nicht aus).
- Die Sperrdämpfung ist bei Stub-Bandpässen prinzipbedingt nur im Bereich um f0
  gut; bei 2·f0 entsteht ein weiterer Durchlass (Spurious-Passband).

#### PCB-Hairpin-Bandpass 2,4 GHz (fünf gefaltete Resonatoren, RT/duroid)

**Preset: PCB Hairpin Bandpass (2.4 GHz)…** lädt einen 5-poligen
Chebyshev-Bandpass (0,1 dB Welligkeit, 10 % Bandbreite → 2,28–2,52 GHz) auf
**RT/duroid 6010LM** (1,27 mm, εr = 10,2, tanδ = 0,0023, 35 µm Kupfer). Fünf
**gefaltete λ/2-Resonatoren** (Hairpins) sind nebeneinander über ihre Armlänge
gekoppelt — ohne Vias, ohne Stichleitungen, Leiterbild nur 25,1 × 14,3 mm.

| Größe | Wert |
|---|---|
| Mittenfrequenz f0 / Bandbreite | 2,40 GHz / 10 % |
| λg im ungeradzahligen Modus | 51,23 mm (εeff = 5,946) |
| Streifenbreite (alle Arme) | 1,186 mm (50 Ω) |
| Armlänge | 11,814 mm |
| Innenabstand der U-Arme | 0,80 mm |
| Faltung außen / elektrisch | 3,172 / 1,986 mm |
| Koppelspalte s0…s5 | 0,316 / 1,387 / 1,753 / 1,753 / 1,387 / 0,316 mm |
| J-Inverter (normiert) | 0,370 / 0,125 / 0,096 / 0,096 / 0,125 / 0,370 |
| Z0e / Z0o | 75,4/38,3 · 57,1/44,5 · 55,2/45,7 Ω |

Synthese: Die beiden Arme eines Hairpins tragen Gegenströme, der Resonator
schwingt also im **ungeradzahligen Modus** des Arm-Paares. Die Armlänge folgt
deshalb εeff,odd = 5,946 (nicht dem Einzelleiter-εeff — damit wären die Arme
~15 % zu kurz). Die Spalte liefert das Hammerstad-Jensen-Modell (even/odd) über
die J-Inverter der Chebyshev-Prototypparameter; die Streifenbreite bleibt
einheitlich, allein der Spalt stellt die Kopplung ein.

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_pcb_hairpin_example.py
```

Verifiziert ist der **Einzelresonator**: ein Hairpin (Arm 11,814 mm, gespeiste
Kopplung 0,316 mm) resoniert bei **2,365 GHz** mit **S21 = −0,16 dB** und
**S11 = −47,6 dB**; die Flankenbreite entspricht Qe ≈ 12,5 (Synthese 13,1).

Hinweise:

- **Simulationsdauer:** Die DFT-Auflösung ist 1/T_sim. Für 240 MHz Durchlass
  mit ~50 MHz Modenabstand braucht es T_sim ≳ 60 ns (≳ 1,2 Mio. Schritte).
  Wegen tanδ = 0,0023 (Q0 ≈ 435) klingt das Sperrband sehr langsam ab. Das
  Preset stoppt deshalb bei `end_criteria = 1e-4` (−40 dB) und
  `max_timesteps = 600_000`.
- **Validierungsstand:** Einzelresonator und Einzelkopplung sind im FDTD
  verifiziert. Kurze Läufe (< 200 k Schritte) lösen den Modenabstand des
  Filters nicht auf und zeigen dann keinen sauberen Durchlass — für die volle
  5-Pol-Kurve den Lauf wie oben verlängern.
- Gitter: `min_cellsize = min(s)/3 = 0,105 mm`, die 1,387-mm-Spalte liegt in
  vier Zellen, Streifenkanten auf Gitterlinien (`edges2grid = "xy"`).

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

**Koordinaten-Ausrichtung (Standard: an).** In *Mesh Parameters* steht
„Align coordinates (float32 grid)". Damit liegen **alle** Geometriekoordinaten
auf demselben float32-Raster, auf dem CSXCAD Boxen/Ports/Dumps speichert — auch
die Dreiecksnetze aus STEP/STL/FCStd. Ein Vertex und eine Boxfläche, die gleich
sein sollen, sind dann bit-identisch (kein zweiter Gitterpunkt, keine
Sliver-Zelle, die den Zeitschritt kollabieren lässt). Ausschalten stellt das
alte Verhalten wieder her (Dreiecksnetze werden dezimal auf 0,1 µm gerundet);
das ist nur für A/B-Vergleiche sinnvoll. Bereits gespeicherte `.npy`-Netze
behalten ihre alte Rundung, bis sie neu importiert werden.

**Zuordnung zu feiner Zellen (P2, Standard: an).** Die Checkbox „Attribute fine
cells to their source" schreibt an jede vom Mesh-Bericht beanstandete Zelle die
Geometrie, die sie erzwingt (z. B. `box:feed + manual`) — sichtbar in den Marken
des Mesh-Viewers und im Bericht unter „Fine-cell sources". Zellen ohne
Autoren-Quelle stammen aus easyMesh selbst (Kreis-/Bogenerkennung, Grading,
PML-Übergang).

**Kritische Zellen im Mesh-Viewer (Standard: aus).** Rechtsklick →
„Mark critical cells (N)" blendet die Zellen ein, die Laufzeit kosten: die
kleinsten Zellen jeder Achse (sie setzen den Zeitschritt), Zellen unter der
geforderten Mindestgröße und — nach einem Lauf — die Zelle, die der Solver als
kleinsten Zeitschritt meldet. Sie erscheinen als gestrichelte Umrisse in den
Schnitten (das Netz bleibt sichtbar) und als Bänder in den *Cell sizes*-Plots;
zusammenhängende Zellen werden zu einer Region zusammengefasst. Grading-Sprünge
und zu große Zellen stehen weiterhin nur im Mesh-Bericht. Die Marken werden
immer berechnet, aber erst auf Wunsch gezeichnet; die Zahl im Menüeintrag sagt
vorher, ob es etwas zu sehen gibt.

**Zellen reparieren (opt-in, Vorschau zuerst).** Rechtsklick auf eine Marke →
„Critical cells (N open)" bietet: *Repair this x/y/z region…* (Vorschau mit
entfernten Linien, neuer kleinster Zelle und dt vorher/nachher; OK speichert die
Regel im Projekt), *Remove the offending line…* (eine Auto-Linie löschen),
*Snap onto the neighbour line…* (nur bei Duplikat-Slivers), *Accept region (do
not repair)* (grau, zählt nicht mehr als offener Befund) und *Explain…* (Ursache,
Quelle, Laufzeit-Strafe). Repariert wird nur, was nicht durch autorisierte
Koordinaten geschützt ist; eine Zelle mit zwei Geometrie-Flächen bleibt und wird
als `geometry` berichtet. Die Reparatur läuft als letzter Schritt der
Mesherzeugung — Vorschau und generiertes Solver-Skript erzeugen dasselbe Gitter.

**Mindestzellgröße erzwingen (V2, Standard: aus).** In *Mesh Parameters* macht
„Enforce min cell size (repair)" aus „Min cell size" eine harte Untergrenze
(„Min cell size (Z)" überschreibt die Schichtdicke): Zellen darunter werden in
den kleineren Nachbarn verschmolzen, das Grading wird neu verteilt; echte Spalte
(beide Flächen aus der Geometrie) bleiben und stehen als „not reached" im
Bericht. Der Schalter ist opt-in, damit bestehende Presets unverändert
reproduzierbar bleiben; easyMesh bekommt die Untergrenze dabei nicht mehr zu
sehen (sonst verfeinert es die lateralen Achsen gegen die Reparatur).

Beides liegt auch im **Mesh-Menü** unter *Critical cells (N open)*: Overlay-
Schalter, Repair/Remove/Snap/Accept/Explain für den schlimmsten offenen Befund
und der Enforce-Schalter. Die Aktionen sind dort nur aktiv, wo sie auch im
Rechtsklick-Menü aktiv wären.

Ausführliche Beschreibung: **F1 → „Mesh Repair"** (auch Help-Knopf im
Reparatur-Dialog und Help → „Mesh repair (critical cells)…").

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
4. Ergebnis-Dateien unter `work\<Projektname>\` (u. a. `project_run.py`, Meshes, S-Params)

### 6.1 Mesh- und Simulationsdiagnose

Zwei Berichte beantworten die Frage „ist dieser Lauf belastbar?" – noch bevor
bzw. nachdem der Solver gelaufen ist:

* **Mesh ➤ Mesh Timing / Feasibility…** (Inhalt steht direkt nach *Build Mesh*
  auch im Tab **Mesh Report** und als eine Zeile im Log): kleinste Zelle je
  Achse und das daraus folgende `dt` (Courant-Grenze im Vakuum plus untere
  Schranke durch das dichteste Dielektrikum), Zellen pro Wellenlänge bei
  `f_max`, der Laufzeit-Faktor einer überfeinerten Zelle gegenüber einer
  typischen Zelle, die Timesteps für einen Durchlauf durch das Gebiet und die
  Frequenzauflösung `1/(max_timesteps·dt)` im Verhältnis zum eingestellten
  Sweep-Raster. Jede Warnung nennt die konkrete Abhilfe (z. B. „max_timesteps
  auf ≥ 389 000 erhöhen" oder welche Zelle zu grob ist).
* **Simulation ➤ Simulation Status…** (nach jedem Lauf, erfolgreich oder nicht;
  derselbe Block wird an das Ende des Logs angehängt): Fehler und Warnungen aus
  dem Solver-Log – einschließlich der Mesh-Validierung von vor dem Lauf –,
  Konvergenz (Endkriterium erreicht oder max. Timesteps), die exakten
  Engine-Werte (`dt`, Nyquist-Rate, Anregungslänge, Operator-/Feldgröße,
  Schritte/Energie/Geschwindigkeit), der Abgleich der Mesh-Schätzung gegen den
  Engine-Wert, die tatsächliche Auflösung des DFT-Fensters und die Passivität
  der S-Parameter (`|S11|²+|S21|² ≤ 1`) über der Frequenz – getrennt nach
  Kernband und Sweep-Rand (eine Überschreitung nur am Rand ist ein
  DFT-Fensterartefakt und harmlos). Am Ende steht ein Fazit
  (`OK` / `WARNING` / `ERROR`) mit „Trust: reliable / use with care / unusable".

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
   `work/<projekt>/sparams/`, NF2FF in `work/<projekt>/nf2ff/`).
   **Polarization…** (Button im Antenna-Tab) zeigt Größen aus demselben
   NF2FF-Ergebnis — ohne neuen Lauf: **Axial Ratio** (dB, Drehsinn
   RHCP/LHCP, Tilt; linear → „linear") und **Phase Center** (Δx/Δy/Δz in mm
   plus RMS des Phasen-Fits) relativ zum NF2FF-Boxmittelpunkt. Der
   Phase-Center-Wert ist eine Schätzung für einen klaren Hauptstrahl; passt
   der Fit nicht, steht „—" mit „phase not well-defined". Details:
   F1 → „Polarization & Phase Center".
4. **2-Port (S2P)** — ein FDTD-Lauf misst **eine** Spalte der S-Matrix (für 2 Ports:
   S11 + S21). Weitere Spalten brauchen weitere Läufe mit anderem erregtem Port
   (`excite_port` umschalten); die JSON-Ergebnisse werden danach zusammengeführt —
   Beispiel: `examples\run_pcb_filter_example.py`.
   Nicht gemessene Einträge bleiben leer (NaN) in `sparams.json`, die Touchstone-Datei
   (`.s2p`) entsteht erst, wenn die Matrix vollständig ist.
5. **Field Viewer** — 3D-Ansicht der Felddumps (F1 für die Hilfe). Die
   Vektor-Pfeile („Vectors") **ersetzen** die farbige Felddarstellung
   (ParaView-Stil), damit keine opake Schnitt-/Oberflächenebene die Pfeile
   verdeckt; mit „field underneath" läßt sich die Überlagerung wieder
   einschalten. Beim manuellen Durchschalten einer Serie meldet die
   Schritt-Anzeige, wie viele Pfeile sich gegenüber dem vorigen Schritt gedreht
   haben — damit ist sofort sichtbar, ob die Pfeile dem Feld wirklich folgen.
   Bei ausgeschaltetem „same length" zeigt die Pfeillänge die Amplitude; die
   Referenz wird einmal über die ganze Serie gemessen, die Längen sind also
   von Bild zu Bild vergleichbar.
   Der Export schreibt zusätzlich eine ParaView-Collection (`<dump>.pvd`) neben
   die Schritt-Dateien, damit die ganze Serie in ParaView aus einer Datei läuft
   — siehe §12.4.

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
| `[ERROR] port_alignment … is not on a mesh line` | Port-Koordinate lag auf keiner Gitterlinie (z. B. Klick im Port-Modus). Die GUI setzt die Port-Koordinaten inzwischen selbst als Gitterlinie (nahe Zwischenlinien werden verschoben, „port alignment: …" im Mesh-Log); nur direkt an einer Metallkante bleibt ein Versatz < 15 % einer Zelle (Warnung `port_snapped`). Nach dem Update **Build Mesh** erneut ausführen. |
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
- **2-Port-Beispiel (Bandpass 5,6 GHz):** `Preset: PCB Bandpass (5.6 GHz)…` oder
  `examples\run_pcb_bandpass_example.py` — 5 offene λ/2-Stubs auf Teflon,
  |S11| < −10 dB von 4,90–6,45 GHz, fertiges `sparams.s2p`.
- **2-Port-Beispiel (Hairpin-Bandpass 2,4 GHz):** `Preset: PCB Hairpin Bandpass
  (2.4 GHz)…` oder `examples\run_pcb_hairpin_example.py` — 5 gefaltete
  λ/2-Resonatoren auf RT/duroid 6010 (εr 10,2, 1,27 mm), kompakt (~25 mm lang),
  ohne Vias, fertiges `sparams.s2p`.

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

---

## 12. Export & Austausch (CAD, CST, ParaView)

Vier Ausgabewege sind für den Datenaustausch gedacht: **STEP** (Geometrie),
**Gitter-JSON** (Mesh-Linien), **Touchstone** (S-Parameter) und **VTK**
(Felddaten). Die ersten drei liest ein anderer Solver wie CST Studio Suite
direkt; VTK ist das neutrale Format für Felder.

### 12.1 STEP — Geometrie nach CAD / CST

**File → Export STEP…**

- Exportiert wird die *effektive* Geometrie, also genau die Primitives, aus
  denen auch das Simulationsskript gebaut wird: analytische Boxen, Zylinder,
  Kugeln und extrudierte Polygone sowie alle importierten Dreiecksnetze.
  Importiertes STEP/FCStd kommt dabei **tesselliert** an (nicht als originales
  BREP) — der Export zeigt, was openEMS tatsächlich rechnet.
- Ausgabe: **AP214 STEP in Millimetern**. Die Zeichnungseinheit aus
  *Mesh → Drawing unit* wird umgerechnet, es gibt also keine Einheiten-Raterei.
- **Objektnamen bleiben erhalten**: jedes Solid kommt in CST unter dem Namen an,
  den es im Mesh Viewer / Projektbaum hat (STEP-`PRODUCT`).
- Eine **null-dicke Metallfläche** (z. B. das `patch` des Patch-Presets) wird
  als **planare Fläche** geschrieben, nicht als degenerierter Solid — openEMS
  bläht sie erst nach dem Meshing auf 2 nm auf, was als CAD unbrauchbar wäre.
- **Geschlossene Netze** werden Solids, **offene Hüllen** bleiben Hüllen. Beides
  ist gültiges STEP und in CST materialisierbar.
- Nicht darstellbare Geometrie (Punkt, Linie, fehlendes Mesh) landet als Warnung
  im Log und wird übersprungen; der Rest der Datei entsteht trotzdem.
- Voraussetzung: **FreeCADCmd** (wie beim STEP-Import), Pfad unter *Settings*.

> Der FreeCAD-Dialog „Export CAD…“ ist die **Gegenrichtung** (FCStd/STEP →
> per-Object-STL + `openems_export.json` für den Importer), kein Geometrie-Export.

### 12.2 Gitter-JSON — identische Zellen in CST

**Mesh → Export grid (JSON)…**

Sowohl openEMS als auch der CST-Zeitbereichslöser rechnen auf einem
rechtwinkligen (hexaedrischen) Gitter. Gibt man CST dieselben Linienpositionen
(z. B. per `Mesh.SetLines`), laufen beide auf **identischen Zellen** — statt auf
zwei unabhängig vermaschten Modellen. Genau dort würden sich die Solver sonst
unterscheiden.

- Quelle ist die CSX des **Mesh Viewers**, also das Gitter, das wirklich gebaut
  wurde (keine Neuberechnung). Vorher „Build Mesh“.
- Alles in **mm**, plus Zellzahl, minimale/maximale Zellgröße und die
  FDTD-Domain. Zylindrische Netze exportieren `rho/phi/z`.

```json
{
  "format": "visualopenems-grid", "version": 1,
  "unit": "mm", "source_unit": "mm", "coord_system": "cartesian",
  "axes": [
    {"name": "x", "lines": [0.0, 0.5, 1.0, 2.0],
     "cells": 3, "cell_size_min": 0.5, "cell_size_max": 1.0}
  ],
  "shape": [3, 2, 2],
  "domain": {"start": [0, 0, 0], "stop": [2, 3, 1.6]}
}
```

### 12.3 CSX XML

**Mesh → Export CSX XML…** bzw. **Open in AppCSXCAD (3D)…**

Schreibt das komplette CSXCAD-Modell (Geometrie + Gitter, ohne Felddaten) für
AppCSXCAD oder ParaView — nützlich zur Kontrolle und zur Übergabe des Modells.

### 12.4 Felddaten — VTK, `.pvd`, MP4

**Simulation → Export field dumps to VTK…**

- Pro Dump eine statische Datei `<dump>.vtr` und — bei Zeitreihen — pro
  gespeichertem Schritt `<dump>_0000.vtr` … plus `<dump>.series.json`
  (Labels, Dateien, Zeiten/Frequenzen, Stride) für den eingebauten Viewer.
- Zusätzlich eine **ParaView-Collection `<dump>.pvd`**: in ParaView öffnen und
  die ganze Serie aus *einer* Datei abspielen. Der Slider-Wert ist die
  **physikalische** Schrittposition (Zeit bei TD-Dumps, Frequenz bei FD-Dumps),
  nicht der Schritt-Index. Die `.pvd` wird aus dem Sidecar neu erzeugt, wenn eine
  aktuelle Serie wiederverwendet wird — ältere Exporte werden so vollständig.
- **Inkrementell**: Ausgaben, die neuer als ihr Dump sind, werden
  wiederverwendet; ein zweiter Klick ist sofort fertig. Der Per-Step-Schalter
  schreibt die Serie (viele Dateien), `step_stride` behält nur jeden N-ten
  Schritt.
- Punktdaten: Skalar `magnitude` (aktiv) und bei xyz-Dumps der Vektor `field`
  (Realteil), in rohen openEMS-SI-Einheiten (E in V/m …).

**Simulation → Export field video (MP4)…** rendert die geladene Serie mit der
aktuellen Field-Viewer-Ansicht (fps aus *Settings*).

### 12.5 Netzwerkdaten — Touchstone

Der S-Params-Viewer schreibt **Touchstone** (`.s1p`, `.s2p`, …) — den Standard,
den CST und openEMS beide lesen. Ein FDTD-Lauf misst **eine** Spalte der
S-Matrix; die Touchstone-Datei entsteht erst, wenn die Matrix vollständig ist
(Teilmatrizen stehen nur in `sparams.json`). Für ein 2-Tor sind also beide
Erregungen nötig (`excite_port` umschalten, Ergebnisse werden zusammengeführt).

### 12.6 Bilder

Jeder Viewer (S-Params, Antenna, Field Viewer) exportiert **PNG**.

### 12.7 Übersicht

| Daten | Format | Richtung | Empfänger |
|---|---|---|---|
| Geometrie | STEP (mm) | raus | CAD, CST, FreeCAD |
| Gitterlinien | grid JSON (mm) | raus | CST-FDTD (Mesh.SetLines) |
| S-Parameter | Touchstone `.sNp` | raus / rein | CST, VNA, scikit-rf |
| E/H-Feldvolumen | VTK `.vtr` + `.pvd` | raus | ParaView |
| Vollmodell (CSX) | CSX XML | raus | AppCSXCAD |

### 12.8 Grenzen

- **STEP transportiert nur Geometrie** — keine Materialien, Ports oder
  Randbedingungen. Diese in CST über die erhaltenen Objektnamen zuweisen.
- **Kein Felddaten-Import**: openEMS-Dumps sind HDF5, CST exportiert eigene
  Formate. Verglichen wird über S-Parameter / Fernfeld oder über VTK in
  ParaView.
- Einheiten werden beim Export nach **mm** umgerechnet (STEP und Gitter-JSON);
  Touchstone bleibt in Hz. Quelle ist die Zeichnungseinheit aus *Mesh*.
