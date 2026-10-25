# Mesh-/Simulations-Diagnose, Field-Viewer-Vektoren, Koordinaten-Probe — Änderungsdoku

> English version: **[DIAGNOSTICS_CHANGES_EN.md](DIAGNOSTICS_CHANGES_EN.md)**

**Umfang:** sieben abgeschlossene Arbeitspakete (sechs Diagnose-/Viewer-Pakete plus
P1 aus dem Quantisierungsplan). Alle Dateien kompilieren, die Testsuite läuft
grün (**256 pytest-Checks**, dazu die Standalone-Smokes), und jede Aussage ist an
echten Projekten aus `work/` gemessen. Es gibt **keine Änderung am
Solver-Verhalten, an Presets oder an bestehenden Projekten** — mit einer
Ausnahme: die Vektor-Darstellung im Field Viewer hat jetzt einen anderen Default
(siehe Änderung 3) und die Koordinaten-Ausrichtung ist standardmäßig an
(Änderung 5, abschaltbar).

**Warum:** Der Entwickler soll *ohne Log-Studium* erkennen, ob ein Lauf sinnvoll
ist (Mesh/Timestep/Frequenzauflösung) und wie belastbar das Ergebnis ist
(Konvergenz, Passivität, Verdikt) — und Feldvektoren sollen sichtbar und korrekt
animiert sein.

---

## Änderung 1 — Mesh-Timing / Machbarkeit direkt nach dem Meshing

**Dateien:** neu `app/meshing/timing.py`; Einbindung in
`app/meshing/build_task.py`; Anzeige über Mesh-Menü → *Mesh Timing /
Feasibility…* (`app/ui/main_window.py`) und den Tab *Mesh Report*.

**Was:** Nach `Build Mesh` wird das gebaute Gitter gegen die eingestellten
Simulationsparameter gerechnet — reine Analyse, kein Solverlauf:

| Größe | Inhalt |
|---|---|
| Zellgrößen | min / typisch / max je Achse, Position der kleinsten Zelle |
| Courant-Schritt | `dt = Δunit / (c₀·√(Σ 1/dᵢ²))` aus den jeweils kleinsten Zellen (= Formel von `openEMS.automesh.mesh_estimate_cfl_timestep`) |
| Dielektrikum | untere Schranke `dt/√εr,max` — im Dielektrikum rechnet der Solver kleiner |
| Auflösung | Zellen pro Wellenlänge bei `f_max` je Achse (Ziel ≥ 10) |
| Timestep-Check | `1/(2·dt)` gegen `f_max` |
| Gebietsdurchlauf | minimale Timesteps für eine Welle quer durch die Domain |
| DFT-Auflösung | `Δf = 1/(NrTS·dt)` gegen das Sweep-Raster (Spanne/200) |
| Laufzeit-Strafe | kleinste Zelle gegen typische Zelle (Faktor) |

**Beispiel `pcb_hairpin`** (5-Pol-Filter, 1,608–3,192 GHz, NrTS 120 000):

```
Mesh timing: dt(CFL, vacuum)=0.3247 ps (>= 0.3247 ps in eps_r=1); 16.1 cells/lambda @ f_max;
min cell 0.1304 mm (5.6x vs. typical); DFT dF=25.7..25.7 MHz for NrTS=120000; 2 warning(s), 0 error(s)
```

* Zellen `53 × 102 × 13 = 70 278`; kleinste Zelle y = 0,1304 mm bei y = 14,13 mm
* Zellen/Wellenlänge: x 21,2 · y 23,8 · z 16,1 (f_max = 3,192 GHz) → ok
* **Laufzeit-Strafe 5,6×** (x-Achse) → Warnung `tiny_cell_slows_run`
* DFT-Auflösung 25,7 MHz gegen 7,92 MHz Sweep-Raster → Warnung
  `resolution_limited` inklusive konkreter Abhilfe („raise max_timesteps to ≥ 388 897")
* Der Solver meldete später `dt = 0.2294 ps` = **71 %** der Vakuum-Schätzung —
  genau die erwartete Dielektrikum-Reduktion (siehe Änderung 2)

**Issue-Codes:** `tiny_cell_slows_run`, `cells_per_lambda_low`,
`timestep_too_coarse`, `timesteps_too_few`, `resolution_limited`,
`empty_mesh`.

**Grenzen:** das ist eine **Schätzung** aus dem Mesh. Den exakten `dt` liefert
nur der Solver (Operator-Aufbau) — deshalb vergleicht Änderung 2 beide Werte.
Die DFT-Auflösung gilt für *volle* `NrTS`; bricht der Lauf früher ab
(Endkriterium), verkürzt sich das Fenster und die Auflösung wird gröber (die
Statusmeldung zeigt dann den *tatsächlichen* Wert).

---

## Änderung 2 — Simulationsstatus nach dem Lauf (Log + Menü)

**Dateien:** neu `app/simulation/report.py` (Qt-frei) und
`app/ui/report_dialog.py` (Fenster mit Copy/Save); Einbindung in
`app/ui/main_window.py` (Simulation-Menü → *Simulation Status…*, automatischer
Log-Anhang nach jedem Lauf).

**Was:** Der Bericht liest das Solver-Log und die S-Parameter und beantwortet
„ist der Lauf durchgelaufen und ist das Ergebnis belastbar?".

* **Engine-Fakten** (aus dem Log): `dt`, Timestep-Methode, Nyquist-Rate
  (TS + Frequenz), Anregungslänge (TS + s), `NrTS` + Verhältnis zur
  Anregungslänge, Zellen, Operator-/Feldgröße, Schritte, Energie, Speed, Laufzeit.
* **Fehler/Warnungen**: Engine-Warnungen, Tracebacks/Crashes, **und die
  Mesh-Validierung von *vor* dem Lauf** (Codes `mesh_*`, als „pre-run mesh"
  gekennzeichnet). Bekannt-harmlose Zeilen werden gefiltert — z. B. die
  `Capacity: -nan(ind)`-Ausgabe der Lumped-Port-Vorbereitung darf **nicht** als
  Fehler erscheinen (Testabdeckung in `tests/test_sim_report.py`).
* **Konvergenz**: Endkriterium erreicht vs. „Max. number of timesteps was
  reached before the end-criteria" (der Satz wird bewusst *nicht* als Konvergenz
  gelesen).
* **Passivität über der Frequenz**: pro *angeregtem Tor* `Σᵢ |Sᵢⱼ|² ≤ 1`
  (bei Anregung an Tor 1 also `|S11|²+|S21|²`). Der Sweep wird in **Kernband**
  (Ränder um 5 % beschnitten) und **Rand** getrennt; eine Überschreitung nur am
  Rand ist ein **DFT-Fensterartefakt** und wird als Info geführt.
* **Design-Qualität**: S11-Minimum (Tiefe + Frequenz), −10-dB-Band, ob die
  Entwurfsfrequenz darin liegt, S21 an der Entwurfsfrequenz.
* **DFT-Fenster** tatsächlich: `Schritte × dt` → `Δf`, verglichen mit dem
  Sweep-Raster.
* **Verdikt + Vertrauensstufe**: `OK`/reliable · `WARNING`/use with care ·
  `ERROR`/unusable.

**Beispiel `pcb_hairpin`** (GUI-Lauf vom 23.10., aus dem Log reproduziert):

```
Verdict   : WARNING - passivity violated in band; not converged (max timesteps); 4 warning(s)
Trust     : use with care
Run       : exit 0 | not converged | 119340/120000 TS | energy -16.3 dB | 57 s
Engine    : dt 0.2294 ps (Rennings_2) | Nyquist 682 TS @ 3.196 GHz
          : cells 54x103x14 = 77868 | operator 3.6 MiB | field 1.8 MiB
          : excitation 15771 TS (3.617 ns) | max TS = 7.61 x excitation
Results   : 201 points | 1.608 GHz .. 3.192 GHz
          : S11 min -5.43 dB @ 2.281 GHz | no -10 dB match in the sweep
          : S21 at the design frequency -41.44 dB
          : passivity max |S|^2 = 1.084 @ 2.25 GHz | in core band 1.084
          : VIOLATIONS inside the core band: 40 point(s) > 1
          : DFT window 119340 TS x 0.2294 ps = 27.37 ns -> dF = 36.5 MHz
            (sweep step 7.92 MHz -> window-limited)
Findings  :
  [INFO]     timestep_check: engine dt 0.2294 ps vs. vacuum Courant estimate 0.3247 ps (71%)
  [WARNING]  not_converged: max timesteps reached before the end criteria
  [WARNING]  passivity_core: |S|^2 sums to 1.084 > 1 at 2.25 GHz (40 point(s))
  [WARNING]  mesh_grading_jump: [pre-run mesh] Axis x: worst neighbor grading 5.58 > 2.0
```

**Grenzen:** Für die Passivitätsprüfung wird die **JSON**-Form der S-Parameter
gebraucht (der Loader liefert komplexe Arrays und wird intern nach dB
umgerechnet). Liegt nur ein Touchstone vor, meldet der Bericht
`results_not_json` (Info) statt stillschweigend „keine Ergebnisse".

---

## Änderung 3 — Field Viewer: Vektorpfeile korrigiert

**Datei:** `app/ui/vtk_viewer.py` (+ Tooltip/In-App-Hilfe `app/ui/tab_help.py`,
Handbuch `docs/ANLEITUNG_DE.md`/`_EN.md`).

Gemeldet waren zwei Symptome; gemessen waren es **drei** Defekte:

**3.1 Überlagerung (Verhalten geändert).** Die Pfeile wurden *zusätzlich* zur
farbigen Slice-/Surface-/Volume-Darstellung gezeichnet; ein opaker Schnitt oder
Körper verdeckt jeden Pfeil, der in ihn hineinzeigt. Neu:
`_show_field_actor()` — bei aktiven Vektoren wird die Felddarstellung **nicht**
gezeichnet (die Pfeile *sind* die Darstellung, wie in ParaView). Die neue
Checkbox **„field underneath"** (`DEFAULT_VECTORS["overlay"]`, Default **aus**)
holt die Überlagerung zurück. Der Farbbalken bleibt, weil er die Pfeilfarben
erklärt.

**3.2 Orientierung ging verloren.** `vtkGlyph3D` orientiert entlang der
*aktiven* Vektor-Arrays des Filtereingangs. Fehlt diese Markierung (Filterkette,
älterer Export), skaliert es die Pfeile nur noch — exakt das gemeldete „nur die
Länge ändert sich". Neu: `sampled.GetPointData().SetActiveVectors(name)` auf dem
Glyph-Eingang.

**3.3 Längenreferenz pro Bild → „Atmen".** Die 99-%-Referenz wurde **pro Bild**
neu berechnet; damit waren Pfeillängen über die Zeit nicht vergleichbar. Neu:
`_vector_reference()` / `_measure_series_reference()` — die Referenz wird
**einmal über die ganze Serie** gemessen (max. 24 Stichproben, Median statt
Maximum, damit der Anregungsimpuls am Serienanfang die Pfeile nicht auf null
schrumpft) und je Serie+Vektorarray gecacht. Ohne Serie gilt weiter das
aktuelle Bild.

**Beweise (Messungen an einem echten Zeitbereichs-Export, 153 Schritte):**

| Prüfung | Ergebnis |
|---|---|
| Richtung Schritt 1 → 6, cos-Median | −0,97 (Surface/Wireframe), −0,99 (Slice), −1,00 (Iso-surface) |
| Anteil gedrehter Pfeile | 92–100 % |
| Rohdaten `field` (Rohdatenvergleich) | cos −0,98, 100 % gedreht |
| Pfeillänge ∝ Amplitude | Längenverhältnis 3,316 gegen |E|-Verhältnis 3,172 |
| Serienreferenz | 0,01426 (Median über 24 von 153 Schritten, Bereich 0…0,228) |
| mit „same length" | konstante Länge 0,003592 in beiden Schritten (wie gewollt) |

**Neu: Bewegungs-Readout.** Beim **manuellen** Durchschalten meldet die
Schritt-Anzeige, wie stark sich die Pfeile gedreht haben, z. B.
`arrows: 24% reversed vs. previous step (median cos +1.00)`. Einmal pro Serie
loggt der Viewer die Längenreferenz:
`VTK viewer: vector length reference |field| = 0.01426 (median over 24 of 153 step(s), …)`.

**Physikalische Einordnung (wichtig für die Erwartung):** animiert sind
**Zeitbereichs-Dumps** (Label `t…` / `step …`) — dort drehen sich die Pfeile mit
dem Feld. **Frequenzbereichs-Dumps** haben keine Zeitachse: der Slider zeigt
`f = … GHz`, und eine über die Frequenz nahezu konstante Modenform bedeutet
konstante Pfeilrichtung — das ist korrekt, keine Anzeigestörung. Beispiel: der
Hairpin-Dump `work/pcb_hairpin/simulation/E_substrate.h5` enthält
`FieldData/FD/f0_real|f0_imag` mit **genau einem** Schritt bei 2,4 GHz.

**Test:** `tests/vtk_vectors_smoke.py` (Standalone — unter pytest stürzt der
Import des VTK-Tabs auf dieser Plattform mit einer Access Violation ab, wie bei
den übrigen VTK-Checks). Das Skript prüft Exklusivmodus, Options-Roundtrip,
Orientierung und Längenreferenz mit einer **Linien-Sonde** statt Pfeilgeometrie
(damit die Richtung direkt ablesbar ist).

---

## Änderung 4 — Koordinaten-Quantisierung: Phase-0-Sonde (nur Messung)

**Datei:** neu `app/meshing/coord_diag.py`; Einbindung in
`app/meshing/build_task.py` (Block im Mesh-Report + eine Logzeile) und in das
Timing-Fenster; Tests `tests/test_coord_diag.py` (21 Checks).

Hintergrund: der Plan `VisualOpenEMS_CoordinateQuantization_Plan` schlägt ein
zentrales Koordinaten-Raster (Lattice) gegen „Mikro-Abweichungen im Mesh" vor.
Phase 0 soll **messen**, nicht ändern.

**Was gemessen wird:**

* **Fast-Dubletten der Gitterlinien**, gebuckelt nach Abstand
  (`<1e-9 … <1e-3` Einheiten) — damit die Größenordnung sichtbar wird.
* **Autoren-Koordinaten-Paare** unter der geplanten Toleranz, **mit
  Quellen-Tag** (`box`, `cylinder`, `polygon`, `stl`, `port`, `dump`, `nf2ff`,
  `manual`) — so ist die Ursache sofort zuordenbar.
* **Abstand jeder Autoren-Koordinate zur nächsten Linie als Bruchteil der
  lokalen Zelle** („on_line" / „snapped" ≤ 15 % / „off_grid" > 15 %). Das ist die
  physikalisch richtige Metrik (dieselbe, die `port_align`/`validators`
  benutzen): Ports > 15 % sind eine Warnung, NF2FF-/Dump-Ecken sind Info.
* **Kleinste Zelle gegen das geforderte `min_cellsize`** (`cell_below_min`).

**Korrigierte Auto-Formel.** Der Plan nennt `q ≤ c_min/1000`, implementiert aber
`decimals = clamp(ceil(log10(c_min)) + 3, 2, 7)` — das widerspricht seinen
eigenen Beispielen (0,1 mm ⇒ 2 statt 4, 0,01 mm ⇒ 2 statt 5). Die Sonde rechnet
mit:

```
N = clamp(ceil(3 - log10(c_min_units)), 2, 7)
```

| `c_min` | N | `q` | Plan-Beispiel |
|---|---|---|---|
| 10 mm | 2 | 10 µm | – |
| 1 mm | 3 | 1 µm | 3 ✓ |
| 0,5 mm | 4 | 0,1 µm | – |
| 0,1 mm | 4 | 0,1 µm | 4 ✓ |
| 0,05 mm | 5 | 10 nm | – |
| 0,01 mm | 5 | 10 nm | 5 ✓ |
| 0,001 mm | 6 | 1 nm | – |

**Messergebnis über 15 echte Projekte aus `work/`** (alle mit Projektdatei und
gespeichertem `csx.xml`):

| Größe | Summe |
|---|---|
| Fast-Dubletten-Gitterlinien (< 1 µm) | **0** |
| Autoren-Paare unter der geplanten Toleranz | **0** |
| Ports weiter als 15 % einer Zelle von der Linie | **0** |
| Koordinaten „knapp daneben" (< 15 % Zelle) | 0–89 je Projekt (harmlos, openEMS snappt) |

Damit ist die Kernprämisse des Plans („doppelte Gitterlinien → winzige Zelle")
für diese Projekte **nicht belegt**. Dasselbe gilt für die Nachbarpfade: zwei
Boxen 0,1 µm bzw. 1 nm auseinander, Box gegen Polyeder, und eine manuelle Linie
1e-7 neben einer Boxkante wurden von easyMesh **verschmolzen** (kleinste Zelle
blieb `min_cellsize`, keine `very_small_cell`-Warnung). Ursache: die App wendet
`f32()` bereits auf **alle** Boxen/Ports/Dumps an, `port_align` setzt
Portkoordinaten auf Linien, und `dedupe_grid_lines` räumt Dubletten auf. Ein
*echter* Spalt (dünne Platte) bleibt dagegen erhalten und erzeugt korrekt eine
`grading_jump`-Warnung.

**Was der Plan trotzdem richtig sieht:** die **eine** messbare Inkonsistenz ist
die Dezimalrundung `np.round(..., 4)` (0,1 µm) im Dreiecks-/STL-Pfad gegen das
sonst überall benutzte `f32`. Beispiel: eine STL-Kante bei 20,00006 mm landet
nach `round(,4)` bei 20,0001 mm und damit **9,918e-5 mm** neben der Boxkante.
Das ist **3,3× bis 830×** größer als die float32-Auflösung (ULP: 0,12 nm bei
1,6 mm · 7,6 nm bei 100 mm · 30,5 nm bei 300 mm) — und damit der einzige
Kandidat, der ein Lattice überhaupt rechtfertigt.

**Nebenbefund (Laufzeit):** Die kleinste Zelle ist in manchen Projekten *weit*
kleiner als angefordert — `cyl_mesh_smoke` 0,045 mm bei 13,3 mm Wunsch
(**297×**), Dipol-cyl 0,051 bei 0,5 (**10×**). Das kostet über `dt` direkt
Laufzeit (siehe Änderung 1) und ist wahrscheinlich die lohnendere Baustelle als
die Doppellinien.

---

## Änderung 5 — P1: ein Koordinaten-Gitter für alle Quellen (schaltbar)

**Dateien:** `app/csxcad_builder/quantize.py` (`align_coordinates()`,
`f32_array()`, `ulp_tolerance()`), `app/csxcad_builder/triangle_mesh.py`,
`app/freecad_io/stl_import.py`, `app/simulation/grid_dedupe.py` (neu),
`app/models/project.py` (`MeshSpec.align_coordinates`),
`app/ui/mesh_params.py` (Checkbox), `app/meshing/build_task.py`,
`app/simulation/script_gen.py`.

**Problem (gemessen):** Boxen/Ports/Dumps/NF2FF gehen durch `f32()`
(float32-Raster), Dreiecksnetze dagegen durch `np.round(..., 4)` (0,1 µm
*dezimal*). Eine Kante bei 20,00006 mm landet dadurch 0,1 µm neben der Boxkante —
das ist 3× bis 830× die float32-Auflösung und war der einzige *messbare*
Quellen-Unterschied (Phase 0).

**Fix:** Beide Pfade benutzen jetzt **dieselbe** Regel (`align_coordinates()`):
Dreiecks-Vertices werden auf das float32-Raster gebracht, auf dem CSXCAD
ohnehin alles speichert. Damit sind ein Vertex und eine Boxfläche, die gleich
sein sollen, **bit-identisch** — kein zweiter Gitterpunkt, kein Sliver-Cell.

**Schalter:** `MeshSpec.align_coordinates` (Default **an**), Checkbox
*Mesh Parameters → „Align coordinates (float32 grid)"*, im Projekt gespeichert.
„Aus" = altes Verhalten (0,1 µm-Dezimalrundung). Alte Projektdateien laden mit
dem Default (an). Bereits gespeicherte `.npy`-Netze behalten ihre historische
Rundung, bis sie neu importiert werden.

**Zusätzlich:** `dedupe_grid_lines` bekommt ein **float32-ULP-Fundament**
(`ulp_align`, 8 ULP der größten Koordinate): Linienpaare unterhalb der
float32-Auflösung verschmelzen auch dort, wo die `frac·cell`-Regel darunter
bleibt. Es wird nie *weniger* zusammengeführt als vorher und ist in allen 15
gemessenen Projekten ein No-op. Die Funktion liegt jetzt in
`app/simulation/grid_dedupe.py` (statt im `SCRIPT_TEMPLATE`-String), damit genau
der ausgeführte Code testbar ist; das generierte Skript importiert sie wie die
übrigen `app.*`-Module (es setzt `APP_ROOT` selbst auf `sys.path`).

**Wirkung (A/B an echten Projekten):** Das *Mesh* ändert sich nicht —
`Dipol_CST_mesh_test` (Zylinder) und `pcb_bandpass` (Boxen) ergeben mit
`align=on/off` identische Gitter (101 bzw. 270 Zellen, gleiche kleinste Zelle).
Genau das war die Phase-0-Vorhersage: keine Doppellinien, die easyMesh nicht
ohnehin verschmilzt. Der Gewinn ist die *Konsistenz der gespeicherten Geometrie*
(Vertex == Boxfläche), nicht ein anderes Gitter.

---

## Änderung 6 — Kritische Zellen im Mesh-Viewer markiert (Rechtsklick)

**Dateien:** neu `app/meshing/critical_cells.py`; `app/ui/mesh_viewer.py`
(Markierungen + Rechtsklick-Menü); `app/meshing/build_task.py`
(`MeshBuildResult.critical_cells`); `app/ui/main_window.py` (Übergabe nach dem
Build, Engine-Zelle nach dem Lauf); Tests `tests/test_critical_cells.py`.

**Was:** Gezeichnet werden nur die Zellen, die **Laufzeit kosten** — die, die
den FDTD-Zeitschritt bestimmen:

| Art | Bedeutung |
|---|---|
| `cell_below_min` | Zelle < ½ der geforderten `min_cellsize` |
| `very_small_cell` | Zelle < `max_cell/500` (Validator-Schwelle) |
| `timestep_cell` | kleinste Zelle ihrer Achse (auf einer *gradierten* Achse) — sie setzt den Zeitschritt |
| `engine_timestep` | die Zelle, die der FDTD-Operator als kleinsten Zeitschritt meldet (`… found at position: 1 : 39;14;0`) — nach dem Lauf |

**Nicht** markiert werden Nachbar-Grading-Sprünge (`grading_jump`) und zu große
Zellen (`cell_too_large`): das sind Qualitätshinweise des Mesh-Berichts, keine
Laufzeit-Problemzellen — sie überdeckten große Teile der Domäne und verdeckten
genau die Zellen, auf die es ankommt. (Die Validator-Warnungen selbst bleiben
unverändert.)

**Darstellung:** Aufeinanderfolgende Problemzellen einer Achse werden zu **einer
Region** zusammengefasst (eine 138-Zellen-Bogenkante = ein Band, nicht 138
Kästen). Achsen-Funde spannen die beiden anderen Achsen über die ganze Domain
(die Auflösung ist an dieser *Position* zu fein) und werden als **gestrichelte
Umrisse ohne Füllung** gezeichnet, damit das Netz sichtbar bleibt. In einer
Schnittansicht erscheint ein Fund der Schnitt-Normalen nicht (er wäre die ganze
Fläche); die Engine-Zelle ist der exakte 3-D-Kasten. In den *Cell sizes*-Plots
zeigt jede Region ein helles Band, die Größe ihrer kleinsten Zelle und einen
Marker. Maximal 24 Marken, nach Schwere sortiert (kleinste zuerst).

**Bedienung:** Rechtsklick im Mesh-Viewer → **„Mark critical cells (N)"**
(ein/aus, Standard **aus**). Die Marken sind eine Diagnose-Überlagerung: berechnet
werden sie immer, gezeichnet erst auf Wunsch — die Zahl im Menüeintrag zeigt
vorher, ob es überhaupt etwas zu sehen gibt. Eine Logzeile pro Build
(`Critical cells: …`) und ein Block im Mesh-Bericht. Nach einem Lauf markiert
der Statusbericht zusätzlich die Engine-Zelle
(`Mesh Viewer: engine_timestep (dt … ps) marked`).

**Verifikation:** 17 echte Projekte aus `work/` — 3–9 Marken je Projekt, keine
überdeckt mehr als 12 % einer Achse (vorher bis 22 volle Ebenen mit Füllung).
Ein gleichmäßig feines Gitter ergibt bewusst keine Marke (es hat keine
Ausnahme-Zelle; der Bericht nennt dort weiterhin die kleinste Zelle). Testnetz
mit einer 1e-6-mm-Linie neben einer Boxkante → 1 `very_small_cell` + die
Achsen-Minima; alle fünf Ansichten zeichnen fehlerfrei; Umschalten entfernt und
setzt die Marken. Gesamtsuite **256 pytest-Checks**.

---

## Änderung 7 — P2: zu feine Zellen werden ihrer Quelle zugeordnet (schaltbar)

**Dateien:** `app/meshing/critical_cells.py` (`attribute_cells()`,
`source_summary()`, `CriticalCell.source`), `app/meshing/build_task.py`,
`app/models/project.py` (`MeshSpec.diagnose_cell_sources`),
`app/ui/mesh_params.py` (Checkbox), `tests/test_critical_cells.py`.

**Was:** Phase 0/P1 haben gezeigt, *dass* Zellen bis 300× zu fein sind. P2 sagt,
**wer sie erzwingt**: für jede markierte Zelle werden beide Flächen gegen alle
Autoren-Koordinaten geprüft (Box-/Zylinder-/Polygon-/STL-Flächen, Ports, Dumps,
NF2FF, manuelle Linien); die nächstgelegene Quelle steht an der Marke.

Beispiel (Testnetz mit einer 1e-6-mm-Linie neben einer Boxkante):

```
Critical cells: 3 (timestep_cell 2, very_small_cell 1)
  very_small_cell (x=1e-06 | box:feed + manual)
  timestep_cell (y=0.0625 40 cells, min 0.0625)
  timestep_cell (z=0.05 2 cells, min 0.05)
Fine-cell sources (which geometry forced them):
     2  easyMesh (no authored source)
     1  box:feed + manual
```

Die 1e-6-mm-Zelle ist eindeutig der manuellen Linie plus der Boxkante zuzuordnen;
die y/z-Minima liegen auf keiner Autoren-Kante („easyMesh") — sie kommen aus der
Gitterverteilung selbst.

**Schalter:** `MeshSpec.diagnose_cell_sources` (Default **an**), Checkbox
*Mesh Parameters → „Attribute fine cells to their source"*. Aus = Marken ohne
Quellenangabe, und der Bericht schreibt „Fine-cell attribution off" statt einer
irreführenden Liste.

**Grenzen:** Zellen *ohne* Autoren-Quelle stammen aus easyMesh selbst
(Kreis-/Bogenerkennung, `interface_refine`, PML-Übergang) — der Bericht nennt sie
„easyMesh (no authored source)" und listet die Kandidaten. Die Zuordnung ist eine
Heuristik (Flächenabstand ≤ 5 % der Zellbreite), keine Ursachenanalyse im Solver.

**Verifikation:** A/B am Testnetz (an/aus) wie oben; Gesamtsuite **256
pytest-Checks**.

---

## Änderung 8 — V1–V4: die Laufzeit-Zellen reparieren (opt-in)

**Dateien:** `app/meshing/repair.py` (neu: `repair_axis`, `preview`,
`apply_repairs`, `apply_mesh_repairs`, `enforced_floors`,
`protected_coordinates`, `repair_report`), `app/meshing/critical_cells.py`
(`cause`, `penalty`, `accepted`, `repairable`), `app/meshing/coord_diag.py`
(`requested_min_cellsize`), `app/meshing/easy_wrapper.py` (`easy_mesh_setup()`,
Reparatur als letzter Schritt, Untergrenze nicht mehr an easyMesh),
`app/meshing/build_task.py`, `app/simulation/script_gen.py`,
`app/models/project.py` (`MeshSpec.mesh_repairs`, `enforce_min_cellsize`,
`accepted_cells`), `app/ui/repair_dialog.py` (neu), `app/ui/mesh_viewer.py`
(Aktionen pro Marke), `app/ui/mesh_params.py` (Checkbox),
`tests/test_mesh_repair.py`, `tests/repair_actions_smoke.py`.

**Was:** Die Änderungen 1–7 haben die Zellen, die den Zeitschritt setzen, nur
*berichtet*. V1–V4 schließen den Kreis, ohne die Diagnose unehrlich zu machen:

* **V1 — die markierte Region reparieren (opt-in, Vorschau zuerst).**
  Rechtsklick auf eine Marke → *Repair this x/y/z region…* öffnet eine Vorschau
  (entfernte Linien, neue kleinste Zelle, dt vorher/nachher, erwartete
  Zellzahl); OK ist gesperrt, solange sich nichts ändern würde. OK speichert
  eine Regel in `MeshSpec.mesh_repairs`. Die Regel läuft in `generate_mesh()`
  als **letzter** Schritt (nach easyMesh, `interface_refine`, Port-Ausrichtung,
  Max-Cell-Deckel und manuellen Linien) — Vorschau und generiertes Solver-Skript
  erzeugen also dasselbe Gitter.
* **V2 — Mindestzellgröße erzwingen (Schalter, Default aus).** *Mesh Parameters
  → „Enforce min cell size (repair)"*. `Min cell size` wird zur harten
  Untergrenze auf jeder Achse, `Min cell size (Z)` überschreibt die
  Schichtdicke. Eine Zelle darunter wird in den kleineren Nachbarn verschmolzen;
  das Grading wird nur um die verschmolzenen Zellen neu verteilt; Zellen über
  dem Max-Cell-Limit werden geteilt. Bei aktivem Schalter bekommt easyMesh die
  Untergrenze **nicht** mehr zu sehen — gemessen an `Filter_new_test1`: easyMesh
  allein reagierte auf `min=0.1` mit Zellen ×2,42 und dt nur ×1,35; ohne die
  Untergrenze in easyMesh ergibt dasselbe Projekt dt ×1,45 bei Zellen ×0,98.
* **V3 — Aktionen pro Marke.** Das Kontext-Untermenü *Critical cells (N open)*
  bietet jetzt **Repair…**, **Remove the offending line…**, **Snap onto the
  neighbour line…**, **Accept region (do not repair)** und **Explain…** — jeweils
  nur aktiv, wo es ehrlich ist: Repair ist bei `geometrie` (beide Flächen
  autorisiert) gesperrt, Snap nur bei Duplikat-Slivers (< 0,1 % einer normalen
  Zelle), Remove nur, wenn mindestens eine Fläche eine Auto-Linie ist.
  Akzeptierte Regionen bleiben sichtbar (grau), zählen aber als akzeptiert
  statt als offener Befund.
* **V4 — ehrliche Diagnose.** `cell_below_min` vergleicht nur noch mit der
  **vom Nutzer gesetzten** Mindestgröße (`requested_min_cellsize`); die
  abgeleiteten Fallbacks (`refined_cellsize`, `target_cell_units`) markierten
  vorher in einem Projekt ohne `min_cellsize` 162 normale Zellen / 5,08 mm.
  Jede Marke nennt jetzt ihre Ursache (`geometrie` > `wunschwert` > `artefakt`;
  `engine` für vom Solver gemeldete Zellen), ihre Laufzeit-Strafe (Achsen-Median
  / Zelle) und ob sie akzeptiert ist. Der Bericht listet je Achse die
  angeforderte Untergrenze und das erreichte Minimum — und sagt es, wenn die
  Grenze nicht erreicht wurde, weil die restlichen Zellen durch autorisierte
  Koordinaten begrenzt sind.

**Schutzregel:** Die Reparatur entfernt nie eine autorisierte Linie —
Domänenkanten, Primitive-/Port-/Dump-/NF2FF-Flächen und manuelle Linien werden
als `protected` übergeben (einmal aus `source_coordinates` berechnet). Eine
Zelle mit **beiden** autorisierten Flächen ist `geometrie` und nicht
reparierbar; eine Zelle mit einer Auto-Fläche darf verschmolzen werden. Der
Gleichheitsvergleich nutzt `_SAME_TOL = 1e-6` (Zeicheneinheiten): CSXCAD
speichert Gitterlinien als float32 (`1.635` → `1.6349999904632568`), die
autorisierten Koordinaten sind float64 — mit 1e-12 löschte eine Reparatur in
`Filter_new_test1` die geschützte Linie 1.635.

**Gemessen (reale Projekte, `build_mesh_and_validate`, gleiche Maschine):**

| Projekt | Variante | dt [ps] | Zellen | dt × | Zellen × |
|---|---|---|---|---|---|
| `Filter_new_test1` | Basis | 0.0790 | 375 570 | 1.000 | 1.000 |
| | V1 (z-Marke) | 0.1157 | 361 660 | **1.465** | 0.963 |
| | V2 (min 0.1) | 0.1143 | 369 150 | **1.447** | 0.983 |
| `Dipol_CST_openems_Test_cyl_z_sym` | Basis | 0.1186 | 2 922 507 | 1.000 | 1.000 |
| | V1 (x-Marke, Default-Floor) | 0.1186 | 2 901 780 | 1.000 | 0.993 |
| | V2 (min 0.5) | 1.2107 | 1 940 057 | **10.207** | 0.664 |

* Filter V1 entfernt das 0,023781-mm-Artefakt; das verbleibende z-Minimum ist
  0,035 mm bei `1.635..1.670672` mit Ursache `geometrie`
  (`box:substrate + box:msl_line`) — der Befund ist jetzt echt, und der Bericht
  sagt das, statt eine Reparatur zu behaupten.
* Filter V2 kann 0,1 mm auf z aus demselben Grund nicht erreichen; die
  Berichtszeile lautet `z: achieved 0.035 mm — not reached: the remaining cells
  are bounded by authored coordinates (the geometry needs them)`.
* Dipol V1 ist mit dem Default-Floor ein No-op: die x/y-Bänder mit 0,051282 mm
  sind ein *Plateau* (beide äußeren Nachbarn gleich groß), deshalb lehnt die
  Default-Regel (eine Einzelzellen-*Delle* verschmelzen) korrekt ab. Der
  funktionierende Weg ist V2 (`min = 0.5`): dt ×10,2 und ein Drittel weniger
  Zellen.
* Attribution am Filter (V2, `min = 0.1`): easyMesh allein verändert das Gitter
  (dt ×1,348, Zellen ×2,417); die Durchsetzung darauf ist dt ×2,108 bei Zellen
  ×0,980. Die Arbeit macht also der Schalter, und easyMesh muss dieselbe Zahl
  nicht mehr jagen.

**Im Projekt gespeichert** (`MeshSpec`; alte Projekte laden mit den Defaults):
`mesh_repairs` (Liste aus `{axis, lo, hi, floor, expand}`), `accepted_cells`
(Liste aus `{axis, lo, hi}`) und `enforce_min_cellsize` (bool, Default `False`).
Die Reparatur ist deterministisch (kein RNG, kein Solver) und entfernt nur
Linien, die sie entfernen darf, oder fügt Teilungslinien über dem Max-Cell-Limit
hinzu.

**Grenzen:** Die Reparatur verschiebt keine autorisierten Linien, ein echter
Spalt (beide Flächen autorisiert) bleibt und wird als solcher berichtet. Einen
Grading-Sprung außerhalb des erweiterten Intervalls kann sie nicht beheben (der
Bericht warnt mit dem verbleibenden Verhältnis). Der zylindrische (polare)
Mesher wird nicht repariert; der Schalter meldet „min-cell enforcement: not
available for the cylindrical mesher“. V2 wirkt nur, wenn `Min cell size` (oder
`Min cell size (Z)`) gesetzt ist.

---

## Nicht geändert

* Solver-Verhalten, Presets, Beispiele und gespeicherte Projekte.
* Die `f32()`-Quantisierung der Boxen/Ports/Dumps/NF2FF, `port_align`,
  `interface_refine` und alle Toleranzen in `validators.py`, `manual_mesh.py`,
  `preview.py` — unverändert. (Geändert wurde nur der *Dreiecks*-Pfad, der jetzt
  auf dasselbe Raster ausgerichtet wird, plus das ULP-Fundament im Dedupe —
  siehe Änderung 5.)
* `SHEET_HALF_THICKNESS = 1e-6` (1-nm-Blatt) und `zero_extent_axes()`.
  Hinweis für später: in float32 ist 1 nm ab ≈ 16 mm Koordinate **nicht
  darstellbar** (`f32(100 mm + 1 nm) == f32(100 mm)`) — das Blatt funktioniert
  heute nur, weil `inflate_sheet_box()` in double (`SetStart/SetStop`) arbeitet.
  Jedes Lattice muss diese Inflation außen vor lassen.
* Menüstruktur (nur neue Einträge: Mesh → *Mesh Timing / Feasibility…*,
  Simulation → *Simulation Status…*) und `AppSettings` (die neuen
  Vektor-Optionen reiten im bestehenden Options-Dict mit).
* Der Standard-Mesh-Pfad: mit *Repair* und *Enforce min cell size* aus (beides
  Defaults) ist das Gitter genau das, was easyMesh vorher erzeugt hat — Presets
  und Beispiele reproduzieren ihre Netze. Die neuen Aktionen pro Marke erweitern
  den bestehenden *Critical cells*-Rechtsklick; nichts läuft von selbst.

---

## Verifikation

```powershell
$PY = "c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe"
cd c:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI

# 1) komplette Suite (enthält die neuen Tests)
& $PY -m pytest tests -q                     # erwartet: 290 passed

# 2) Standalone-Smokes (VTK/GUI laufen NICHT unter pytest)
& $PY tests\mesh_thread_smoke.py             # MESH THREAD SMOKE OK
& $PY tests\vtk_viewer_smoke.py              # VTK VIEWER SMOKE PASS
& $PY tests\vtk_vectors_smoke.py             # VTK VECTORS SMOKE PASS
& $PY tests\mesh_params_flow.py              # ALL PASS
& $PY tests\mesh_viewer_smoke.py             # MESH VIEWER SMOKE PASS
& $PY tests\repair_actions_smoke.py          # REPAIR ACTIONS SMOKE PASS
& $PY tests\tab_help_smoke.py                # TAB HELP SMOKE PASS

# 3) In der App
#    Mesh -> Build Mesh           -> Logzeilen "Mesh timing: …" und "Coord probe: …"
#    Mesh -> Mesh Timing / Feasibility…   -> beide Blöcke im Fenster
#    Simulation -> Run Simulation -> am Log-Ende der Block "SIMULATION STATUS"
#    Simulation -> Simulation Status…     -> derselbe Block mit Copy/Save
```

Erwartete Logzeilen (Beispiel, Zahlen projektabhängig):

```
Mesh timing: dt(CFL, vacuum)=0.3247 ps (>= 0.3247 ps in eps_r=1); 16.1 cells/lambda @ f_max;
min cell 0.1304 mm (5.6x vs. typical); DFT dF=25.7..25.7 MHz for NrTS=120000; 2 warning(s), 0 error(s)
Coord probe: 0 near-duplicate line pair(s) (<0.001 mm), 0 authored pair(s) below 0.0005 mm,
0 coordinate(s) off the grid (0 of them ports), 14 snapped; smallest cell 0.25 mm (1.0x min_cellsize)
```

---

## Offene Punkte / nächste Schritte

Phase 0 hat die Planprämisse für die 15 Projekte **nicht** bestätigt. Empfehlung
daher, gestaffelt nach Nutzen/Risiko:

1. ~~**P1 (klein, risikofrei):** `np.round(...,4)` im Dreiecks-/STL-Pfad durch
   dieselbe Regel ersetzen, die überall sonst gilt (`f32`, optional
   `f32(snap(v))`) — beseitigt die einzige gemessene Quellen-Inkonsistenz
   (0,1 µm) ohne Mesh-Risiko. `dedupe_grid_lines` auf **`max(τ, frac·cell)`**
   erweitern (nie schwächer als heute) + Regressionstest.~~ **erledigt**
   (Änderung 5): Dreiecks-Pfad auf das float32-Raster ausgerichtet (schaltbar,
   Default an), Dedupe mit ULP-Fundament; die `max(τ, frac·cell)`-Regel war
   bereits implementiert.
2. **P2 (der eigentliche Gewinn):** die 10–300× zu feinen Zellen aufklären —
   *welche Primitive* erzeugen sie (Kandidaten: Arc-/Kreiserkennung,
   `interface_refine`, PML-Übergang)? Die Sonde um den Primitive-Namen erweitern,
   dann gezielt eingreifen. Das liefert genau den versprochenen Nutzen
   (stabiler CFL-Schritt), nur an der richtigen Stelle.
3. **P3 (nur falls nötig):** Mesh-Editor-Linien auf ein **sichtbares** Raster
   snappen (UI-„Snap 1 µm"), Modellfelder + Dialog aus dem Plan; erst danach ein
   Lattice als Ganzes — mit der korrigierten Auto-Formel, `τ = max(q/2, 8·ULP)`
   und der Regel „nie zwei Flächen desselben Materials zusammenziehen".

**Plandokument-Korrekturen (offen):** in
`VisualOpenEMS_CoordinateQuantization_Plan\Coordinate_Quantization_Plan_DE.md` +
`_EN.md` sind noch einzuarbeiten: die falsche Auto-Formel (Zeile 84), der
100-mm-float32-Wert (7,63e-6 statt 1,2e-5), `max(τ, frac·cell)` statt Ersetzen,
„auto" als Default, die Blatt-/Material-Kollaps-Regel, das Snappen beim
*Anwenden* (nicht nur beim Erzeugen) sowie Abnahmekriterien für die E2E-Tests.
Das Messergebnis dieser Phase 0 gehört als eigener Abschnitt in den Plan.

---

## Betroffene Dateien

| Bereich | Datei | Art |
|---|---|---|
| Mesh-Timing | `app/meshing/timing.py` | neu |
| Mesh-Timing | `app/meshing/build_task.py` | erweitert (Timing + Coord-Probe am Result) |
| Simulationsstatus | `app/simulation/report.py` | neu |
| Simulationsstatus | `app/ui/report_dialog.py` | neu |
| GUI | `app/ui/main_window.py` | Menüs, Log-Anhänge, Fenster |
| GUI | `app/ui/tab_help.py` | Hilfe-Text Vektoren |
| Field Viewer | `app/ui/vtk_viewer.py` | Exklusivmodus, Orientierung, Serienreferenz, Readout |
| Koordinaten-Probe | `app/meshing/coord_diag.py` | neu |
| P1 Koordinaten | `app/csxcad_builder/quantize.py` | `align_coordinates()`, `f32_array()`, `ulp_tolerance()` |
| P1 Koordinaten | `app/csxcad_builder/triangle_mesh.py` | Vertices auf dem float32-Raster (`align`-Flag) |
| P1 Koordinaten | `app/freecad_io/stl_import.py` | speichert volle Präzision (Rundung erst beim CSX-Aufbau) |
| P1 Koordinaten | `app/simulation/grid_dedupe.py` | neu — aus dem Skript-Template extrahiert, mit ULP-Fundament |
| P1 Koordinaten | `app/simulation/script_gen.py` | importiert `grid_dedupe`, übergibt `align`/`ulp_align` |
| P1 Koordinaten | `app/models/project.py`, `app/ui/mesh_params.py` | `MeshSpec.align_coordinates` + Checkbox |
| Kritische Zellen | `app/meshing/critical_cells.py` | neu (Marken für den Mesh-Viewer) |
| Kritische Zellen | `app/ui/mesh_viewer.py`, `app/meshing/build_task.py`, `app/ui/main_window.py` | Marken, Rechtsklick-Menü, Übergabe |
| P2 Quellen | `app/meshing/critical_cells.py` | `attribute_cells()`, `source_summary()`, `CriticalCell.source` |
| P2 Quellen | `app/models/project.py`, `app/ui/mesh_params.py` | `MeshSpec.diagnose_cell_sources` + Checkbox |
| Tests | `tests/test_critical_cells.py` | erweitert (Zuordnung, Schalter, Fallback) |
| Tests | `tests/test_critical_cells.py` | neu |
| Tests | `tests/test_dedupe_grid_lines.py` | neu |
| Tests | `tests/test_quantize.py`, `tests/test_axis_aligned_box.py` | erweitert (Bit-Identität, Legacy-Offset, ULP) |
| Tests | `tests/test_mesh_timing.py`, `tests/test_sim_report.py`, `tests/test_coord_diag.py` | neu |
| Tests | `tests/vtk_vectors_smoke.py` | neu |
| Tests | `tests/vtk_viewer_smoke.py` | Options-Dict um `overlay` erweitert |
| Doku | `docs/ANLEITUNG_DE.md` / `_EN.md` (+ `ANLEITUNG_DE.docx`) | Abschnitte 6.1 und 7.5 |
| Doku | `CHANGELOG.md` | Einträge unter *Unreleased* |
| Doku | dieses Dokument | neu |
