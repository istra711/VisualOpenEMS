# Anleitung: Port in FreeCAD markieren

> English version: **[FREECAD_PORTS_EN.md](FREECAD_PORTS_EN.md)**

**Ziel:** In FreeCAD so markieren, dass die GUI beim Import **automatisch einen openEMS-Port** anlegt (Name, 50 Ω, Start/Stop, Richtung).

**Voraussetzungen**

| Was | Wert |
|-----|------|
| FreeCAD | 1.1.4 (`c:\FreeCAD_1.1.4-Windows-x86_64\`) |
| Einheiten | **View → Units → mm** |
| Dokumenttyp | **FCStd** speichern (STEP/STL speichern Ports normalerweise **nicht**) |
| GUI-Import | **Import Geometry…** oder **FreeCAD… → Export CAD…** |

**Empfohlen:** Variante A (Punkte `PORT_*`).  
**Alternativ:** Variante B (Custom Properties am Solide) oder C (Dokument-JSON).

---

## Warum Ports in FreeCAD?

- Geometrie **und** Feed-Markierung bleiben im CAD-Dokument.
- Bei jeder Re-Import/Export muss der Port nicht mehr händisch neu angelegt werden.
- Die GUI liest beim Import/Export `ports` und erzeugt **PortDef**-Einträge im Fenster **Ports / Dumps** (Geometry-Menü, **Strg+Umschalt+D**).

---

## Variante A — Port-Punkte (empfohlen)

### 1. Feed-Punkte anlegen

Jeder Port braucht **zwei Punkte**:

| Label (Name) | Bedeutung |
|--------------|-----------|
| `PORT_<name>_start` | Start des Port-Vektors |
| `PORT_<name>_stop` | Ende des Port-Vektors |

**Namensschema:** `PORT_` + Portname + `_start` / `_stop`  
Beispiele: `PORT_in1_start`, `PORT_in1_stop` · `PORT_probe_start`, `PORT_probe_stop`

**So in FreeCAD:**

1. Menü **Part → Vertex** (oder Draft Point).
2. Im Tree **umtaufen / Label** setzen: exakt `PORT_in1_start` bzw. `PORT_in1_stop`.
3. Punkte an die **Feed-Position** setzen (Placement oder Task-Bearbeitung).
4. **File → Save** als `.FCStd`.

> Wichtig: **Label** (nicht nur interner Name) muss dem Schema entsprechen — beide werden gelesen.

### 2. Wo sitzt der Port? (Patch-Antenne 2,45 GHz)

Beispiel Patch (mm), Z = Höhe, GND bei z ≈ −1…0, Patch oben:

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

Vektor quer durch die Lücke des Treibers.

### 3. Weitere Ports

Pro Port ein weiteres Paar, z. B.:

```text
PORT_in1_start  / PORT_in1_stop     # Anregung (excite = true)
PORT_probe_start / PORT_probe_probe  # Messung (excite = false)
```

### 4. Import in der GUI

1. GUI starten: `python app\main.py`
2. **Import Geometry…** → `.fcstd` **oder** **FreeCAD… → Export CAD…** (Häkchen Import)
3. Log prüfen: Hinweis zu `Ports auto-added`
4. **Geometry → Ports / Dumps…** (**Strg+Umschalt+D**): Port(s) erscheinen (z. B. `in1`, R = 50, excite an)
5. Optional im Fenster **Ports / Dumps** prüfen/ändern (Edit Port… / Remove Port)
6. **Mesh → Mesh Parameters…** → **Build CSX + Mesh + Validate (Strg+M)** → **Simulation → Run Simulation (Strg+R)**

PORT-Punkte werden **nicht** als Meshes tesselliert (nur als Marker gelesen).

### 5. Beispiel-Skript (optional)

**Dipol 2,45 GHz mit fertigen Ports (empfohlen als Startmodell):**

```text
python examples\run_dipole_ports_example.py
```

Erzeugt unter `work\dipole_ports\`:

| Datei | Inhalt |
|-------|--------|
| `dipole_2p45.FCStd` | Geometrie + `PORT_in1_*` + `openems_ports` JSON + Props |
| `dipole_2p45.step` | reine Geometrie (ohne Port-Marker) |
| `openems_ports.json` / `openems_export.json` | Port-Definition für Import |
| `dipole_params.json` | Maße (λ, L, Gap) |

Geometrie: Halbwellen-Dipol, Draht Ø2 mm, Lücke 2 mm, Port `in1` quer durch die Lücke (−1 mm → +1 mm auf X).

**Nur FCStd erzeugen (FreeCADCmd):**

```text
set DIPOLE_OUT_DIR=C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\dipole_ports
FreeCADCmd examples\freecad_dipole_ports.py
```

Smoke-Test: `tests\dipole_ports_smoke.py`

**Allgemeines Marker-Demo (Punkte anlegen):**

```text
FreeCADCmd examples\freecad_add_port_marks.py
```

Erzeugt `PORT_in1_start/stop` am ersten Solide und speichert  
`work\freecad_export\port_marks_example.FCStd`.

Env (optional):

```text
set FREECAD_SOURCE=C:\...\meine.FCStd
set FREECAD_OUT_DIR=C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export
```

---

## Variante B — Custom Properties am Solide

Am Objekt (z. B. `patch`) **Custom Property** über **Add Property** (oder Property-Editor):

| Property | Typ | Beispiel |
|----------|-----|----------|
| `openems_port_name` | String | `in1` |
| `openems_port_type` | String | `lumped` |
| `openems_port_start` | String / Vector | `-6, 0, -1` oder `FreeCAD.Vector(-6,0,-1)` |
| `openems_port_stop` | String / Vector | `-6, 0, 1.6` |
| `openems_port_r` | Float | `50` |
| `openems_port_excite` | Bool | `true` |

- Property-Name muss **exakt** `openems_port_…` beginnen.
- Typen: `lumped` · `msl` · `rect_waveguide` (sonst `lumped`).
- FCStd speichern → Import wie oben.

---

## Variante C — Dokument-JSON

Dokument-Eigenschaft `openems_ports` (String) mit JSON-Liste:

```json
[
  {
    "name": "in1",
    "type": "lumped",
    "start": [-6.0, 0.0, -1.0],
    "stop": [-6.0, 0.0, 1.6],
    "resistance": 50.0,
    "direction": "z",
    "excite_port": true
  }
]
```

Freecad-Tools-Tipp im GUI nennt diesen Weg ebenfalls. Variante A ist meist schneller.

---

## Was die GUI daraus macht

| Feld | Herkunft |
|------|----------|
| Name | Label `PORT_<name>_…` / `openems_port_name` / `name` |
| Start/Stop | Punkte / Props / JSON |
| Richtung | `direction` oder Settings-Default oder größtes Δ der Vektorachse |
| R | CAD-Wert, sonst **Settings → Port defaults** (Default 50 Ω) |
| Excite | CAD-Wert, sonst Settings (Default true) |
| Typ / Mesh dirs | CAD-Wert, sonst Settings |

**Einstellbar in der GUI:**

1. **Settings… → Port defaults** — Typ, Widerstand, Excite, Mesh dirs, Richtung  
   (gilt für FreeCAD-Import und als Seed für *Add Port…*; explizite `openems_port_*`-Werte gewinnen immer)
2. **Geometry → Ports / Dumps…** (**Strg+Umschalt+D**) — Doppelklick zum Editieren, *Edit Port…* / *Remove Port*

Fehlstart/identische Punkte → Port wird übersprungen (Log-Warnung möglich).

---

## Häufige Fehler

| Problem | Lösung |
|---------|--------|
| Port fehlt im Log / Ports / Dumps | Label/Name prüfen: `PORT_in1_start` + `_stop` |
| Nur `_start` vorhanden | Beide Punkte nötig, gleiche Namensbasis |
| Identische Punkte | Start ≠ Stop, Vektor ≠ 0 |
| Port am falschen Ort | Punkte auf Feed-Lücke / Patch–GND |
| STEP-Import ohne Ports | **FCStd** verwenden (oder Custom Props/JSON — bei purem STEP oft verloren) |
| Port nicht excitiert | GUI → Geometry → Ports / Dumps… → **Excite** an Port 1 |
| Mesh fehlt am Port | Mesh Parameters: höheres Preset; Ports **vor** Build prüfen |

---

## Checkliste

- [ ] Modelleinheit **mm**
- [ ] Feed-Punkte mit Label `PORT_in1_start` / `PORT_in1_stop`
- [ ] Vektor spannt die Port-Öffnung (Patch ↔ GND oder Dipol-Lücke)
- [ ] **FCStd** gespeichert
- [ ] GUI **Import Geometry…** oder **Export CAD…**
- [ ] Log: Ports auto-added
- [ ] Geometry → **Ports / Dumps…** prüfen (50 Ω, Excite)
- [ ] **Build CSX + Mesh + Validate (Strg+M)** → **Run Simulation (Strg+R)** → S-Params prüfen
- [ ] Gesamt-Workflow: **[FREECAD_WORKFLOW_DE.md](FREECAD_WORKFLOW_DE.md)**

---

## Verwandte Dateien

| Datei | Zweck |
|-------|--------|
| `app\freecad_io\port_marks.py` | Extraktion FreeCAD → PortDef |
| `examples\freecad_dipole_ports.py` | Dipol-Beispiel mit PORT-Markern |
| `examples\run_dipole_ports_example.py` | Dipol generieren + Ports verifizieren |
| `examples\freecad_export_for_openems.py` | Export + `ports` im Manifest |
| `examples\freecad_add_port_marks.py` | Demo: Port-Punkte anlegen |
| `tests\dipole_ports_smoke.py` | Smoke Dipol-Ports |
| `app\ui\tab_help.py` | GUI-Hilfe Tabs / Fenster (Geometry / Ports / …) |
| `docs\FREECAD_WORKFLOW_DE.md` | Gesamt-Workflow FreeCAD → Import → Simulation |
| `docs\ANLEITUNG_DE.md` | Kompletter Workflow FreeCAD → Simulation |
| `docs\ANLEITUNG_DE.md` | Kompletter Workflow FreeCAD → Simulation |
