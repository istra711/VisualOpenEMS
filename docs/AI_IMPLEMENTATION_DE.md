# KI-Implementierung

Dieses Dokument beschreibt die Implementierung der KI-Funktionen in
VisualopenEMS: die deterministische Diagnose, das OpenCode-CLI-Backend,
Kontext- und Prompt-Aufbau, den lokalen Read-only-Index für
OpenEMS/CSXCAD-Wissen, sichere Korrekturen, den Projektgenerator,
strukturierte Projekt-Patches, den strukturellen Preflight, die Qt-Integration
und die Tests.

Es richtet sich an Maintainer und fortgeschrittene Nutzer, die genau wissen
wollen, was läuft, welche Daten das Programm verlassen und wo die
Sicherheitsschranken liegen.

> **Status:** Phase 1 (Fehlerassistent), Phase 2 (Projektgenerierung und
> strukturierte Änderungen) und die lokale Read-only-Wissensschicht sind
> implementiert. Mehrvariablen-Optimierung und ein Study-Objekt sind für eine
> spätere Phase geplant und nicht Teil dieses Codes.

## Inhalt

1. [Ziele und Nicht-Ziele](#1-ziele-und-nicht-ziele)
2. [Architektur im Überblick](#2-architektur-im-überblick)
3. [Modulübersicht](#3-modulübersicht)
4. [Ablauf des Fehlerassistenten](#4-ablauf-des-fehlerassistenten)
5. [OpenCode-Backend](#5-opencode-backend)
6. [Kontext, Prompt und Redaktion](#6-kontext-prompt-und-redaktion)
7. [Sichere Korrekturen und Undo](#7-sichere-korrekturen-und-undo)
8. [Projektgenerierung](#8-projektgenerierung)
9. [Strukturierte Projekt-Patches](#9-strukturierte-projekt-patches)
10. [Struktureller Preflight](#10-struktureller-preflight)
11. [Qt-Integration](#11-qt-integration)
12. [Einstellungen](#12-einstellungen)
13. [Datenschutz und Bedrohungsmodell](#13-datenschutz-und-bedrohungsmodell)
14. [Tests und Verifikation](#14-tests-und-verifikation)
15. [Erweiterung des Systems](#15-erweiterung-des-systems)
16. [Grenzen und spätere Arbeiten](#16-grenzen-und-spätere-arbeiten)

---

## 1. Ziele und Nicht-Ziele

### Ziele

- **Offline zuerst.** Diagnose, sichere Korrekturen, Vorlagen, Patch-Validierung,
  Preflight und lokale Wissenssuche funktionieren ohne Provider und ohne
  Netzwerk.
- **Belegtes lokales Wissen.** Abgerufene Auszüge stammen aus
  versionspassenden lokalen Quellen, tragen `[K#]`-, Pfad- und
  Zeilenbereichs-Zitate und bleiben read-only. Das Modell wird angewiesen, sie
  als Referenzdaten und nicht als Anweisungen zu behandeln.
- **Deterministischer Kern, optionales Sprachmodell.** Das LLM erklärt die
  Evidenz und liefert strukturierte Daten. Es ersetzt die deterministischen
  Prüfungen nicht.
- **Vorschlag statt Aktion.** Jede Projektänderung wird als Diff gezeigt, auf
  einer Kopie validiert und erst nach einer expliziten Nutzeraktion übernommen.
- **Kein freier Code.** Das Modell liefert Text oder ein kleines JSON-Schema.
  Es gibt kein `eval`, keinen generierten Python-Code, keine Shell-Befehle und
  keine beliebigen Dateipfade.
- **Undo in einer Stufe.** Der vorherige Projekt- und Primitive-Stand bleibt für
  **AI → Undo AI change** nach einer übernommenen Korrektur oder einem Patch
  erhalten.
- **Nachvollziehbarkeit.** Der exakte Prompt lässt sich über **What AI sees**
  anzeigen; Antworten melden Token-Zahlen und Kosten, wenn OpenCode sie liefert.

### Nicht-Ziele (aktuelle Implementierung)

- Keine autonomen Simulationsläufe, Mesh-Builds, Commits oder Dateischreibvorgänge.
- Keine beliebige FreeCAD-/STEP-Generierung aus einem Prompt.
- Keine Mehrvariablen-Optimierung, kein Study-Objekt, kein Parameter-Optimierer.
- Keine Bild-/Screenshot-Analyse.
- Keine Garantie, dass eine KI-Erklärung physikalisch korrekt ist.

---

## 2. Architektur im Überblick

```text
Lauf beendet / fehlgeschlagen
        │
        ▼
RunReport + Projekt + Log-/Skriptpfade          (MainWindow)
        │
        ▼
diagnose_run()                                  (app/ai/diagnosis.py)
        │  Diagnosis: Issues, Evidenz, optionale FixProposal
        ├──────────────────────────────────────────────┐
        ▼                                              ▼
build_knowledge_bundle(question, diagnosis)   Projekt-/Report-/Logpfade
        │  KnowledgeBundle: [K1]...-Auszüge           │
        └──────────────────────┬───────────────────────┘
                               ▼
build_run_context() -> Projektdigest, Report,   (app/ai/context.py)
gefiltertes Log, optionales Skript, Diagnose, Wissen
        │
        ▼
build_prompt() -> feste Anweisungen + Zitate    (app/ai/context.py)
        │
        ▼
AiWorker (QThread)                              (app/ai/worker.py)
        │
        ▼
OpenCodeBackend: opencode run --format json     (app/ai/backend.py)
        │  JSONL-Events: text, step_finish, error
        ▼
Chat-Antwort (nur lesend)
        │
        ├── Nutzer wählt eine FixProposal
        ▼
apply_fix() auf Deep Copy -> Typ-/Bereichsprüfung -> Projekt-JSON-Roundtrip
        │
        ▼
explizites Apply in der GUI -> In-Place-Update -> Snapshot für Undo
```

Projektgenerierung und Projektbearbeitung nutzen dasselbe Backend und denselben
Worker, aber einen anderen Prompt und einen anderen Validierungspfad:

```text
Vorlagenformular / KI-Vorschlag          Projektanweisung
        │                                     │
        ▼                                     ▼
build_template()                    project_patch_prompt()
        │                                     │
        ▼                                     ▼
preflight(candidate)                JSON-Patch parsen -> auf Kopie anwenden
        │                                     │
        └──────────────┬──────────────────────┘
                       ▼
             preflight(candidate)
                       │
                       ▼
          Diff + Annahmen im Dialog
                       │
                       ▼
          explizites Apply / Create project
```

---

## 3. Modulübersicht

| Pfad | Aufgabe |
|------|---------|
| `app/ai/types.py` | Dataclasses `FixChange`, `FixProposal`, `DiagnosisIssue`, `Diagnosis`, `KnowledgeHit`, `KnowledgeBundle`, `RunContext`. |
| `app/ai/diagnosis.py` | Qt- und providerfreie Abbildung von `RunReport`-Findings und Projektprüfungen auf priorisierte Issues und Fixes. |
| `app/ai/fixes.py` | Strikte Allow-List, Typ-/Bereichsprüfung, Deep-Copy-Anwendung, Projekt-JSON-Roundtrip, optionales In-Place-Commit. |
| `app/ai/context.py` | Kompakter Projektdigest, gefilterte Log-/Skriptauszüge, Redaktion und Prompt-Aufbau mit Längenlimits. |
| `app/ai/knowledge.py` | Qt-freie Read-only-Wurzeln, sicherer Textscan, Zeilen-Chunks, BM25-ähnliches Ranking, Versionserkennung und 120-s-Indexcache. |
| `app/ai/backend.py` | `AiBackend`-Interface, `OpenCodeBackend` (`opencode run --format json`), `MockBackend`, JSONL-Event-Parsing. |
| `app/ai/worker.py` | `AiWorker` (Qt-Thread) mit Streaming-Chunks, Done/Failed-Signalen und Abbruch. |
| `app/ai/templates.py` | Vorlagenregister, Parameterschema und reine Fabriken für sieben validierte Designs. |
| `app/ai/preflight.py` | Strukturelle Kandidatenprüfung; kein Mesh-Build und kein Solver. |
| `app/ai/project_patch.py` | Deklarative Patch-Operationen, strikte Schema-Prüfung, Diff-Text und Copy-on-Write-Anwendung. |
| `app/ai/generation.py` | Defensives JSON-Extrahieren/-Validieren für Vorlagenvorschläge und Projekt-Patches sowie Prompt-Builder. |
| `app/ui/ai_panel.py` | Rechtes Dock: Diagnoseansicht, Fix-Liste, Diff, Chat, Wissensstatus, Quellen und Prompt-Vorschau. |
| `app/ui/ai_settings_dialog.py` | Provider, Kommando, Modell, Timeout, Sprache, Kontext- und Wissenschafts-Einstellungen. |
| `app/ui/project_generator.py` | Vorlagenformular, KI-Vorschlag, Vorschau/Preflight, Erzeugen. |
| `app/ui/project_patch_dialog.py` | Anweisung, KI-Vorschlag, Diff/Preflight, Apply. |
| `app/ui/main_window.py` | AI-Dock/Menü-Verdrahtung, Run-Report-Erfassung, Fehlerboxen, Apply/Undo, Projektinstallation, Wissensabruf und Result-Invalidierung. |
| `app/settings.py` | `ai_*`-Einstellungen und Defaults. |
| `build_exe.py` | PyInstaller-Hidden-Imports für die KI-Module und Dialoge. |
| `tests/test_ai_assistant.py` | Phase-1-Unit-Tests: Diagnose, Fixes, Kontext, Backend-Parsing. |
| `tests/test_ai_phase2.py` | Phase-2-Unit-Tests: Vorlagen, Preflight, Patch-Schema, JSON-Extraktion. |
| `tests/test_ai_knowledge.py` | Wissenstests: Wurzeln, sicherer Scan, Chunking, Ranking, Budget, Cache, Versionen, deutsche Aliase und Prompt-Zitate. |
| `tests/ai_assistant_smoke.py` | Offscreen-GUI-Smoke für AI-Dock, Diagnose, Apply/Undo, lokales Wissen und Prompt-Vorschau. |
| `tests/project_generator_smoke.py` | Offscreen-GUI-Smoke für Generator-Vorschau, Installation, Patch-Apply/Undo und beide KI-Dialoge. |

Die Module unter `app/ai/` importieren kein Qt. Dadurch bleibt der
deterministische Kern headless testbar, und ein künftiges Backend (zum Beispiel
das GitHub-Copilot-SDK) kann dieselbe Diagnose-, Kontext- und Validierungsschicht
verwenden.

---

## 4. Ablauf des Fehlerassistenten

### 4.1 Erfassung des Run-Reports

Wenn eine Simulation endet oder fehlschlägt, speichert `MainWindow`:

- das `RunReport`-Objekt (`_last_run_report`),
- das Arbeitsverzeichnis (`_last_run_work`),
- den Logpfad (`_last_run_log_path`),
- den Exit-Code (`_last_run_exit_code`),
- eine kurze Fehlermeldung (`_last_run_message`).

Fehlermeldungsboxen und der Dialog **Simulation Status** erhalten einen
**Explain with AI**-Button. Dieselbe Diagnose lässt sich über
**AI → Explain last run** oder im Dock öffnen.

Ein neues Projekt, ein Projektimport, ein generiertes Projekt oder ein
angewendeter Patch setzen diesen Zustand zurück, damit ein alter Report nie
einem neuen Projekt zugeordnet wird.

### 4.2 Deterministische Diagnose

`diagnose_run(project, report, note=...)` erzeugt eine `Diagnosis`:

- `verdict` und `headline` aus dem Run-Report,
- `issues` mit `severity`, `code`, `title`, `detail`, `evidence` und optional
  `fixes`,
- `checked` als Liste der durchgeführten Prüfungen,
- `context_note` für die Fehlermeldung.

Die Abbildung umfasst:

| Code | Bedeutung |
|------|-----------|
| `exit_code` | Solverprozess fehlgeschlagen; Ergebnisse sind nicht vertrauenswürdig. |
| `no_results` | Solver erfolgreich, aber keine S-Parameter-Datei geschrieben. |
| `results_not_json` | Touchstone vorhanden, aber kein `sparams.json`; Passivitätsprüfung entfällt. |
| `max_ts_reached` | Timestep-Budget vor der Konvergenz erschöpft. |
| `energy_vs_criteria` | Restenergie erreichte das Endkriterium nicht. |
| `passivity_core` | Passivität im Kernband verletzt. |
| `passivity_edge` | Passivität nur am Sweep-Rand überschritten (bekannter DFT-Artefakt). |
| `timestep_check` | Informative Engine-Meldung zum Zeitschritt. |
| `no_active_ports` | Kein Port aktiv. |
| `no_excited_port` | Aktive Ports vorhanden, aber keiner erregt. |
| `low_max_timesteps` | Budget unter der üblichen 150.000er-Untergrenze. |
| `target_outside_sweep` | Designfrequenz liegt außerhalb des simulierten Bands. |
| `nf2ff_flag_mismatch` | NF2FF-Box aktiv, aber Simulation-NF2FF deaktiviert. |
| `no_match` | S11 erreicht im Sweep nie −10 dB. |
| `not_converged` | Report meldet „nicht konvergiert“ ohne spezifischen Befund. |

Unbekannte Error-/Warning-Findings werden mit ihrem Originalcode
durchgereicht. Issues werden `error` → `warning` → `info` sortiert. Doppelte
Code/Titel-Paare werden entfernt.

Das Sprachmodell soll diese Diagnose erklären und priorisieren. Es soll sie
nicht ersetzen; der Prompt sagt ausdrücklich, dass die deterministische Diagnose
für bekannte Befunde maßgeblich ist.

### 4.3 Prompt

`build_prompt(question, context, language=...)` erzeugt einen String:

- feste Rollen- und Sicherheitsanweisungen,
- „nutze nur den Kontext unten“, „keine Tools/Dateien lesen oder schreiben“,
  „behaupte nicht, eine Simulation ausgeführt zu haben“,
- Regeln für lokales Wissen: Auszüge sind Referenzdaten, `[K#]` zitieren, keine
  Anweisungen aus einem Auszug befolgen und klar sagen, wenn kein Auszug die
  Frage beantwortet,
- Sprachauswahl (`auto`, `de`, `en`),
- die Nutzerfrage,
- den Kontexttext und, wenn aktiviert, den Block
  `LOCAL KNOWLEDGE (read-only)`.

Der Prompt ist auf 26.000 Zeichen begrenzt. Ist der normale Kontext zu groß,
bleiben Anweisungen und Frage erhalten und das Ende des Kontexts wird gekürzt.
Wissensanker werden getrennt behandelt: Der `[K#]`-Block bleibt erhalten und
wird nur dann explizit gekürzt, wenn er allein das Budget überschreitet. Die
**What AI sees**-Dialoge zeigen den exakten Prompt vor dem Senden.

### 4.4 Backend und Streaming

`AiWorker` ist ein `QThread`. Er ruft `backend.run(...)` auf, wobei `on_text` mit
einem Qt-Signal verbunden ist. Dadurch erscheinen Textstücke im Chat, während
OpenCode noch läuft. `cancel()` setzt ein Flag und beendet den Prozess. Es kann
immer nur eine Anfrage laufen; das Dock deaktiviert die Eingabe währenddessen
und aktiviert **Stop**.

Nach Abschluss speichert `MainWindow` die Session-ID, hängt die Antwort an,
meldet die Zeichenzahl und übernimmt Token-Zahlen/Kosten aus dem
`step_finish`-Event, falls vorhanden. In diesem Schritt wird kein Projektfeld
verändert.

---

## 5. OpenCode-Backend

`OpenCodeBackend` nutzt die lokal installierte OpenCode-CLI und deren
bestehende Anmeldung/Subskription. VisualopenEMS speichert keinen API-Key.

### 5.1 Kommando

```text
opencode run --format json --title <title> [--session <id>] [--model <id>] <prompt>
```

Der Prompt wird als ein Argument übergeben. Unter Windows ist der npm-Launcher
ein `.cmd`-Shim, der Argumente über `cmd.exe` leitet und mehrzeilige Argumente
verstümmelt. `resolved_command()` löst deshalb einen `.cmd`/`.bat`-Shim auf die
echte `opencode.exe` daneben auf:

```text
<prefix>/node_modules/@opencode/cli/bin/opencode.exe
```

Ist nur der Shim verfügbar, werden Zeilenumbrüche als letzter Ausweg
abgeflacht, statt den Kontext stillschweigend zu verlieren.

### 5.2 Prozessumgebung

- Arbeitsverzeichnis: ein neutrales temporäres Verzeichnis
  (`%TEMP%/visualopenems-ai`), nicht das Projekt- oder Arbeitsverzeichnis.
- `stdin=DEVNULL`, stdout/stderr gepuffert, UTF-8 mit Replacement.
- `OPENCODE_DISABLE_AUTOUPDATE=1` und `NO_COLOR=1`.
- Verstecktes Konsolenfenster unter Windows (`hidden_console_kwargs()`).
- Timeout-Timer (Default 180 s, einstellbar 30…1800 s) beendet den Prozess.
- Cancel beendet den Prozess und liefert „Request cancelled.“

### 5.3 JSONL-Events

Jede stdout-Zeile wird einzeln geparst. Unbekannte oder unvollständige Zeilen
werden zu `{"type": "raw"}` und ignoriert.

| Event | Behandlung |
|-------|------------|
| `text` mit `part.type == "text"` | `part.text` anhängen; an die UI streamen. |
| `step_finish` | `tokens`, `cost`, `reason` in die Reply-Metadaten übernehmen. |
| `error` | Eine lesbare Fehlermeldung sammeln. |
| alles andere | Ignorieren. |

Wurde kein Text erzeugt, werden Fehlertext, der stderr-Rest (letzte 6.000
Zeichen) oder der Exit-Code als Fehler zurückgegeben. Prompts über 28.000
Zeichen werden vor dem CLI-Aufruf mit einem expliziten Marker gekürzt.

### 5.4 Sessions

Die von OpenCode zurückgegebene Session-ID wird in `_ai_session_id` gespeichert
und für Folgefragen im selben Projekt wiederverwendet. Das gibt dem Chat
Kontinuität, ohne die ganze Historie erneut zu senden. Neues/Öffnen eines
Projekts, generiertes Projekt und angewendeter Patch setzen die Session zurück.

### 5.5 Backend-Interface

```python
class AiBackend:
    name = "backend"
    def is_available(self) -> bool: ...
    def run(self, prompt, *, title, session_id, model, timeout_s, cwd,
            on_text, should_stop) -> AiReply: ...
    def cancel(self) -> None: ...
```

`AiReply` trägt `text`, `session_id`, `meta` und `error`. `MockBackend` ist eine
deterministische Implementierung für Tests und GUI-Smokes. Ein künftiges
Copilot-SDK-Backend muss nur dieses Interface erfüllen; Diagnose, Kontext, Fixes
und UI bleiben unverändert.

---

## 6. Kontext, Prompt und Redaktion

### 6.1 Projektdigest

`project_digest()` sendet nie das vollständige Projekt-JSON. Enthalten sind:

- Projektname, Zeicheneinheit, Frequenzbereich und Zielfrequenz,
- Randbedingungen, PML-Größe und Luftrand,
- Mesh-Preset, Zellen pro Wellenlänge, minimale/maximale/verfeinerte Zellgröße,
- Simulationsflags: max. Timesteps, Endkriterium, Felddumps, NF2FF,
- NF2FF aktiv/Box-Modus/Start/Stop/Frequenzmodus/Theta-Phi-Schritte,
- Anzahl Primitive, Ports, Dumps, Variablen und Materialien,
- Variablen mit Werten, Ausdrücken und Kommentaren (bis 40),
- Ports mit Typ, Aktiv-/Excite-Flags, Phase, Widerstand, Start/Stop, Richtung
  und Reverse-Flag,
- Dumps mit Quantity, Enabled und Frequency-Domain-Flag (bis 30),
- Primitive-Zusammenfassungen mit Kind, Name, Material, Start/Stop, Center,
  Radius und Ausdrücken (bis 40, danach „and N more“),
- die Sweep-Zusammenfassung, falls ein Sweep existiert.

Der Digest wird als Ganzes redigiert.

### 6.2 Logauszug

`read_log_excerpt()` behält Zeilen mit Error/Warning/Failure/Exception/
Traceback, Mesh/PML/Nan/Divergence/Abort/Exit-Code, Timestep/Nyquist/Energy/
Convergence/S-Parameter/Post-Processing-Schlüsseln. Jeder Treffer bekommt eine
Kontextzeile; die letzten 30 Zeilen werden angehängt. Zeilen werden nummeriert
und auf 300 Zeichen gekürzt. Default: 160 Zeilen und 18.000 Zeichen.

### 6.3 Skriptauszug

`read_script_excerpt()` behält die ersten 40 Zeilen sowie Zeilen mit FDTD, Run,
Ports, Boundaries, Excitation, NF2FF, CalcPort, Export, Timestep, End Criteria,
PML, Mesh und SetMesh. Default: 140 Zeilen und 14.000 Zeichen. Der Auszug lässt
sich in den AI-Einstellungen deaktivieren.

### 6.4 Redaktion

`redact_text()` ersetzt:

- das aktuelle Home-Verzeichnis durch `%USERPROFILE%`,
- `C:\Users\<Name>` durch `C:\Users\<user>` (auch mit Forward Slashes),
- OpenAI-artige `sk-...`-Tokens,
- GitHub-`ghp_...`- und `github_pat_...`-Tokens,
- `Authorization: Bearer ...`-Werte,
- `api_key=...` / `api-key: ...`-Werte.

Absolute Arbeits-/Projektpfade außerhalb des Benutzerprofils können weiterhin
sichtbar sein. Der Vorschaudialog sagt das ausdrücklich.

### 6.5 Lokale Wissenssuche

`app/ai/knowledge.py` ist eine Qt-freie Read-only-Schicht. Das Sprachmodell
bekommt kein Werkzeug zum Lesen von Dateien; die Anwendung baut einen kleinen
Index und fügt nur die besten Treffer in den Prompt ein.

**Wurzeln.** `discover_roots()` liefert eine fokussierte Liste in
Prioritätsreihenfolge:

| Wurzel | Inhalt |
|--------|--------|
| VisualopenEMS | `README.md`, `CHANGELOG.md`, `docs/**`, `examples/**`, `app/ui/tab_help.py`. |
| easyMesh | Python-, Markdown-, reST- und Textdateien unter `third_party/easyMesh4openEMS/**`. |
| openEMS | `python/Tutorials`, `docu` und `include` unter dem konfigurierten openEMS-Root. |
| openEMS/CSXCAD-Pakete | Die Ordner `openEMS` und `CSXCAD` in `site-packages` des Simulations-Python. |
| Extra | `ai_knowledge_paths`, getrennt durch `;`, Zeilenumbrüche oder den Plattform-Pfadtrenner. |

Der große openEMS-Installationsbaum wird bewusst nicht komplett gescannt
(Qt, Octave, FreeCAD, pyems und Third-Party-Tools würden Tausende irrelevante
Dateien beisteuern).

**Sicherer Scan.** Gelesen werden nur `.py`, `.pyi`, `.pxd`, `.pxi`, `.md`,
`.rst`, `.txt`, `.html`, `.htm`, `.c`, `.h`, `.hh`, `.hpp`, `.cc`, `.cpp`,
`.inc` und `.m`. Binärdateien (NUL-Byte), versteckte Dateien, Symlinks,
`__pycache__`, VCS-Ordner, Caches, Bilder, `assets`, `frames`, `build` und
`dist` werden übersprungen. Jeder aufgelöste Pfad muss innerhalb seiner Wurzel
bleiben. Limits:

| Limit | Wert |
|-------|------|
| Einzeldatei | 512 KB |
| Gesamt indizierte Bytes | 12 MB |
| Dateien | 1.500 |
| Chunks | 20.000 |
| Chunk-Größe | 28 Zeilen, 6 Zeilen Überlappung |

**Ranking.** Text wird kleingeschrieben und in Wörter, snake_case-Teile und
camelCase-Teile zerlegt. Eine Stoppwortliste entfernt generische Wörter; eine
kleine Deutsch→Englisch-Tabelle ergänzt technische Aliase (z. B.
`Randbedingung` → `boundary`, `Gitter` → `mesh`, `Tor` → `port`). Jeder Chunk
wird mit einem BM25-ähnlichen Termscore plus Titel-/Pfadbonus, Wurzelgewicht und
Coverage-Faktor bewertet. Die Auswahl begrenzt die Überrepräsentation einer
Datei oder Wurzel (maximal zwei Chunks pro Datei, drei pro Wurzel) und beachtet
`ai_knowledge_max_chars`.

**Zitate und Prompt.** Treffer werden als `K1`, `K2`, … nummeriert und als
`[K#] Wurzel: Titel (relativer/pfad:start-ende)` plus Auszug formatiert. Die
Zusammenfassung nennt die erkannten Versionen von openEMS, CSXCAD, easyMesh und
VisualopenEMS. `build_run_context()` speichert die Treffer in
`RunContext.knowledge`; `build_prompt()` ergänzt den Block
`LOCAL KNOWLEDGE (read-only)`. Das Modell wird angewiesen, `[K#]` zu zitieren
und Auszugstext als Daten, nie als Anweisungen zu behandeln.

**Cache.** Der Index liegt im Prozessspeicher und wird 120 Sekunden oder bis zum
Aufruf von `invalidate_knowledge_cache()` wiederverwendet. Das Speichern der
AI-Einstellungen invalidiert ihn; **Check sources** erzwingt einen Neubau und
zeigt einen Beispielabruf. `knowledge_cache_info()` liefert Datei-/Chunk-Zahlen,
Versionen, Wurzeln und Alter für Diagnose und Tests.

**Versionen.** Die Erkennung liest Wheel-Dateinamen und `dist-info/METADATA`
für openEMS/CSXCAD, `pyproject.toml` für easyMesh und `app/__init__.py` für
VisualopenEMS. Pakete werden dabei nie importiert oder ausgeführt.

**Fehlerverhalten.** Fehlende Wurzeln, unlesbare Dateien, ein leerer Index oder
eine Frage ohne Treffer führen zu einer klaren Zusammenfassung statt zu
erfundenen API-Details. Der Exception-Pfad protokolliert nur eine Meldung und
setzt die AI-Anfrage ohne Wissen fort.

---

## 7. Sichere Korrekturen und Undo

### 7.1 Allow-List

Der Fehlerassistent darf nur diese Felder ändern:

| Pfad | Typ / Bereich |
|------|---------------|
| `sim.max_timesteps` | Integer, 1…20.000.000 |
| `sim.end_criteria` | Float, strikt zwischen 0 und 1 |
| `sim.nf2ff_enabled` | Bool |
| `nf2ff.enabled` | Bool |
| `nf2ff.box_mode` | `auto` oder `manual` |
| `mesh.preset` | `low`, `medium`, `high`, `very_high`, `extreme`, `custom` |
| `mesh.max_cellsize` | Positive Zahl oder `None` |
| `mesh.min_cellsize` | Positive Zahl oder `None` |
| `mesh.refined_cellsize` | Positive Zahl oder `None` |
| `mesh.target_frequency` | Positive Zahl |
| `boundary.pml_size` | Integer, 1…64 |
| `ports[i].active` | Bool |
| `ports[i].excite_port` | Bool |
| `ports[i].resistance` | Positive Zahl |
| `ports[i].excite_phase_deg` | 0…360 |

Geometriepositionen, Materialien, Primitivgrößen und Portkoordinaten sind
absichtlich nicht enthalten. Projektgenerierung und der strukturierte
Patch-Dialog sind die getrennten Pfade für Geometrie-/Materialänderungen.

### 7.2 Validierung und Anwendung

`apply_changes()`:

1. kopiert das Projekt tief,
2. löst jeden exakten Pfad auf (`ports[0].excite_port` wird unterstützt),
3. prüft Typ und Bereich,
4. setzt jeden Wert auf der Kopie,
5. serialisiert und deserialisiert das ganze Projekt (`Project.from_dict`),
6. gibt erst dann die Kopie zurück oder committet sie in-place.

Das Hauptfenster führt zusätzlich den normalen Variablen-Resolver aus. Schlägt
ein Schritt fehl, bleibt das Live-Projekt unverändert und der Fehler wird
angezeigt.

`apply_fix()` startet keinen Solver, baut kein Mesh und speichert kein Projekt.
Der Statustext sagt das ausdrücklich.

### 7.3 Undo

Vor einer angewendeten Korrektur oder einem Patch speichert `MainWindow` eine
Deep Copy von Projekt und Primitiven in `_ai_undo`. **AI → Undo AI change**
stellt beides wieder her, löscht den Snapshot, aktualisiert Formular/Ports/
Viewer-Domain und setzt den AI-Kontext zurück. Undo gilt für eine Stufe: Ein
zweites Apply ersetzt den Snapshot, und das Laden eines anderen Projekts löscht
ihn.

---

## 8. Projektgenerierung

### 8.1 Vorlagen

`app/ai/templates.py` registriert sieben Vorlagen:

| ID | Titel | Wesentliche Parameter |
|----|-------|-----------------------|
| `patch` | Probe-fed Patch-Antenne | f0, Bandbreite, eps_r, Dicke, Patch W/L, Speiseposition/-widerstand |
| `yagi` | Yagi-Uda-Antenne | f0, Bandbreite, Element-/Boom-Radius, Speisespalt |
| `pcb_filter` | Microstrip-Notchfilter | f0, eps_r, tanδ, Dicke, Leiterbahnbreite/-hälfte |
| `pcb_bandpass` | Open-Stub-Bandpass | f0, eps_r, tanδ, Dicke |
| `hairpin` | Hairpin-Bandpass | f0, relative Bandbreite, eps_r, Dicke |
| `gps_patch` | GPS-L1-CP-Patch | f0, eps_r, Dicke, Patchseite, Eckenabschnitt, Speise-Inset |
| `wr62` | WR62-Wellenleiter | f0, Länge, erregter Port |

Jeder `TemplateParam` deklariert Key, Label, Default, Kind (`float`, `int`,
`choice`, `text`), Einheit, Minimum, Maximum, Dezimalstellen, Schrittweite,
Choices und Hilfetext. Frequenzen sind im UI-Schema GHz, Maße mm; die Fabriken
rechnen an den Stellen in Hz um, an denen das Projektmodell Hz erwartet.

`ProjectTemplate.build(values)`:

- zwingt Werte in die deklarierten Bereiche,
- lehnt unbekannte Keys ab,
- ruft eine reine Fabrik auf, die `(Project, primitives, notes)` liefert,
- hängt `project.primitives` an.

Die Fabriken nutzen dieselben Geometriegeneratoren wie die Presets
(`app/csxcad_builder/primitives.py`, `gps_patch.py`, `hairpin.py`). Generierte
Projekte sind normale Projekte: Variablen, Materialien, Ports, Dumps, Mesh und
Solver-Einstellungen bleiben editierbar.

### 8.2 KI-Vorschlag

`template_suggestion_prompt()` sendet den Vorlagenkatalog und das vollständige
Parameterschema. Das Modell muss genau ein JSON-Objekt liefern:

```json
{
  "template": "patch",
  "title": "2.45 GHz FR4 Patch",
  "parameters": {"f0": 2.45, "eps_r": 4.4, "thickness": 1.6},
  "assumptions": ["Startmaße"]
}
```

`extract_json_object()`:

- akzeptiert einen eingezäunten Markdown-Block oder reinen Text,
- findet das erste ausbalancierte Top-Level-Objekt,
- berücksichtigt Strings und Escapes,
- liefert eine lesbare Fehlermeldung bei leerem, fehlendem, ungültigem oder
  unvollständigem JSON.

`parse_template_suggestion()` lehnt unbekannte Vorlagen-IDs und Parameter-Keys
ab, validiert `choice`-Werte, konvertiert Zahlen und begrenzt Annahmen auf 20
Einträge. Das Formular wird nur bei einer gültigen Antwort gefüllt.
**Preview / preflight** bleibt eine separate Nutzeraktion.

### 8.3 Vorschau und Erzeugen

Der Generator-Dialog baut den Kandidaten mit `template.build()`, führt
`preflight()` aus und zeigt:

- die Zusammenfassung des generierten Projekts,
- KI-Annahmen (falls vorhanden),
- den vollständigen Preflight-Text.

**Create project** ist nur ohne Preflight-Fehler aktiv. Warnungen sind erlaubt.
Das Erzeugen eines Projekts:

- fragt vorher nach, wenn der aktuelle Editor ungespeicherte Änderungen hat,
- ersetzt den Editorinhalt durch das generierte Projekt,
- markiert es als ungespeichert (nicht automatisch auf Disk),
- löscht alte Ergebnisse, Referenzdaten, gestagte Runs und Sweep-Zustand,
- leert Mesh-/Result-Viewer,
- setzt AI-Session/Report/Undo zurück,
- schreibt Zusammenfassung und Preflight ins Geometrie-Info-Panel und Log.

Der Nutzer muss **File → Save As** verwenden, um es zu behalten.

---

## 9. Strukturierte Projekt-Patches

### 9.1 Operationen

`parse_project_patch()` akzeptiert höchstens 100 Operationen. Unbekannte
Operationsnamen werden abgelehnt. Erlaubt sind:

| Operation | Payload |
|-----------|---------|
| `set` | `path`, `value` für ein sicheres skalares Projektfeld |
| `set_variable` | `name`, `value`, optional `expression`, `comment` |
| `remove_variable` | `name` |
| `set_port` | `index` oder `name`, `fields` |
| `add_port` | `fields` inkl. Name, Typ, Start, Stop |
| `remove_port` | `index` oder `name` |
| `add_primitive` | `fields` inkl. Kind und Name |
| `update_primitive` | `name`, `fields` |
| `remove_primitive` | `name` |
| `add_material` | `fields` inkl. Name und Kind |
| `update_material` | `name`, `fields` |
| `set_mesh` | `fields` |
| `set_sim` | `fields` |
| `set_nf2ff` | `fields` |
| `set_boundary` | `fields` |

### 9.2 Feldvalidierung

- Portfelder nutzen `PortDef`-Dataclass-Namen. `start`/`stop` müssen drei
  endliche Zahlen sein; Bools müssen Bools sein; numerische Felder müssen Zahlen
  sein; `type` muss `lumped`, `msl`, `rect_waveguide`, `waveguide` oder `probe`
  sein; `expressions` muss ein Objekt sein.
- Primitivfelder nutzen eine feste Allow-List: kind, name, material, priority,
  start/stop/center, radius, points, axis, coord, length, expressions,
  edges2grid, ratio, corner/inner/outer radius, angle, thickness. Kinds sind
  `box`, `cylinder`, `sphere`, `polygon`, `wire`, `curve`, `stl`, `lumped`.
- Materialfelder nutzen `MaterialDef`-Dataclass-Namen. Kinds sind `pec`,
  `dielectric`, `conducting_sheet`, `lumped_rlc`, `ignore`; Farbe muss vier
  Komponenten haben.
- `set_mesh`, `set_sim`, `set_nf2ff` und `set_boundary` akzeptieren nur
  existierende Dataclass-Felder. Der Wert wird gegen den aktuellen Feldtyp
  geprüft (bool/int/float/list/str); `*_file`, `path` und `source` werden
  abgelehnt, damit ein KI-Patch das Projekt nicht auf eine neue Datei zeigen
  kann.
- `add_*` verlangt einen eindeutigen Namen. `update_*`/`remove_*` lösen über
  Name oder Index auf und schlagen fehl, wenn das Ziel nicht existiert.
- Variablennamen laufen durch die normale `validate_variable_name()`-Prüfung.

### 9.3 Anwendung und Diff

`apply_project_patch(project, primitives, patch)`:

1. kopiert Projekt und Primitive tief,
2. wendet jede Operation der Reihe nach an,
3. bricht beim ersten Validierungsfehler den ganzen Vorschlag ab (das Original
   bleibt unverändert),
4. führt `apply_changes()` für skalare `set`-Pfade aus,
5. setzt `project.primitives` nur bei Erfolg auf die neue Liste.

Der Dialog führt danach `preflight()` aus, zeigt `patch.diff_text()` plus
Annahmen und den Preflight-Report und aktiviert **Apply** nur ohne Fehler.

Das Anwenden eines Patches in `MainWindow`:

- speichert das vorherige Projekt/die Primitive für Undo,
- ersetzt Projekt und Primitive,
- löscht Ergebnisse, S-Parameter-Referenz, gestagten Run, Sweep-Zustand und
  Mesh-Signatur,
- leert Mesh-/Result-Viewer,
- setzt AI-Diagnose/Session-Kontext zurück,
- zeigt den Diff im Geometrie-Info-Panel und loggt die Änderung.

Es baut nie das Mesh, startet keinen Solverlauf, committet kein gestagtes
Ergebnis und speichert keine Datei.

---

## 10. Struktureller Preflight

`preflight(project, primitives)` liefert einen `PreflightReport` mit Errors,
Warnings und Notes. Er ist bewusst unabhängig von Qt, easyMesh und dem
tatsächlichen CSX-Build.

### Prüfungen

- **Serialisierung:** Projekt-JSON-Roundtrip (`Project.from_dict`).
- **Variablen:** Fehler aus `resolve_variable_defs()`.
- **Primitive:** Objektform, Namensvorhandensein/-eindeutigkeit,
  Kind-Vorhandensein, Materialexistenz, Ausdrucksauflösung, Bounding-Boxes,
  doppelte Namen.
- **Materialien:** doppelte Namen, bekanntes Kind, positive
  Epsilon/Perfähigkeit.
- **Domain:** geschätzte Geometrie- + Luftrand-Box; Primitive außerhalb sind
  Warnungen.
- **Mesh:** Zeicheneinheit, Preset, positive Start-/Stop-Frequenzen, Stop über
  Start, Ziel im Sweep, Zellen pro Wellenlänge, positive Zellgrößen, Min ≤ Max.
- **Solver:** positive max. Timesteps, Endkriterium strikt zwischen 0 und 1.
- **Boundary:** jede Fläche muss `PEC`, `PMC`, `MUR` oder `PML` sein.
- **Ports:** doppelte Namen, bekannter Typ, Ausdrucksauflösung, gültige endliche
  Start/Stop, Länge ungleich null, positiver Widerstand, Phase 0…360,
  Domain-Enthaltensein und (für Lumped-/MSL-/Probe-Ports) Metallkontakt.
- **Quellen:** mindestens ein aktiver Port (Error), mindestens ein erregter Port
  (Warning).
- **Dumps/NF2FF:** Ausdrucksauflösung, doppelte Dump-Namen, NF2FF-Box-Modus
  (`auto`/`manual`), Volumen ungleich null, Box innerhalb der geschätzten
  Domain und `sim.nf2ff_enabled`-Flag.
- **Leere Projekte:** keine Geometrie oder keine Materialien erzeugen Warnungen.

Errors blockieren **Apply** / **Create project**; Warnungen sind informativ. Der
Report sagt ausdrücklich, dass er kein Mesh-Build und keine EM-Simulation ist.

---

## 11. Qt-Integration

### 11.1 Dock und Signale

`AiAssistantPanel` wird versteckt in einem rechten `QDockWidget` erzeugt
(`Ctrl+Shift+A`). Signale:

```text
send_requested(str)      stop_requested()
apply_fix_requested(obj) undo_requested()
settings_requested()     preview_requested()
help_requested()         explain_requested()
clear_requested()
```

`MainWindow` besitzt den Backend-/Worker-Lebenszyklus, baut Diagnose/Kontext/
Prompt, ruft lokales Wissen ab, wendet Fixes an und speichert Undo-Snapshots.
Das Panel rendert nur und sendet Signale; es berührt das Projekt nicht.

### 11.2 Aufbau

- Kopf: Provider-Status, Kontextzeile, **Settings**, **Help**.
- Wissenszeile: `Local knowledge: N excerpt(s) · Versionen`; **Sources…** öffnet
  eine Read-only-Liste mit Pfaden, Zeilenbereichen und Auszügen.
- Diagnosegruppe: Verdict, Checked-Liste, Issues mit Severity/Evidenz,
  Fix-Auswahl, **Apply fix**, **Undo**, **Explain last run**.
- Chatgruppe: Transkript, Eingabe (Strg+Enter sendet), **Send**, **Stop**,
  **What AI sees**, **Clear**.

### 11.3 Menüeinträge

```text
AI → AI Assistant                 (Dock)
AI → Explain last run…
AI → Generate project…
AI → Edit project with AI…
AI → Undo AI change
AI → Local knowledge sources…
AI → AI settings…
AI → AI help…
Help → AI assistant (error analysis)…
Help → AI project generation and edits…
Help → AI implementation details…
```

Fehlermeldungsboxen und der Simulation-Status bieten zusätzlich **Explain with
AI**.

### 11.4 Zustandsresets

`_reset_ai_project_state()` löscht Report, Arbeits-/Logpfade, Exit-Code,
Fehlermeldung, Session, letzten Prompt, Wissensbundle und Undo-Snapshot,
deaktiviert Undo und leert Dock-Diagnose/Chat/Wissen. Es wird beim Laden eines
anderen Projekts, beim Installieren eines generierten Projekts und beim
Anwenden eines Patches aufgerufen. Das Speichern der AI-Einstellungen
invalidiert zusätzlich den Wissenscache und setzt die Wissenszeile zurück.

---

## 12. Einstellungen

Gespeichert in `app_settings.json` (git-ignoriert):

| Key | Default | Bedeutung |
|-----|---------|-----------|
| `ai_provider` | `opencode` | `offline` oder `opencode`. |
| `ai_opencode_cmd` | `opencode` | Kommando oder vollständiger CLI-Pfad. |
| `ai_model` | leer | Optionale Provider-/Modell-ID; leer = OpenCode-Default. |
| `ai_timeout_s` | `180` | Prozess-Timeout in Sekunden (30…1800). |
| `ai_answer_language` | `auto` | `auto`, `de` oder `en`. |
| `ai_max_log_lines` | `160` | Maximale gefilterte Logzeilen im Prompt. |
| `ai_include_script` | `true` | Skriptauszug mitsenden. |
| `ai_knowledge_enabled` | `true` | Lokalen Read-only-Wissensindex aktivieren. |
| `ai_knowledge_paths` | leer | Zusätzliche Ordner/Dateien; `;`, Zeilenumbruch oder Plattformtrenner. |
| `ai_knowledge_max_chars` | `8000` | Zeichenbudget für Wissensauszüge (begrenzt auf 1.200…24.000). |

**AI settings → Test OpenCode** prüft nur, ob das Kommando auflösbar ist. Es
sendet keinen Prompt. **Check sources** baut den Index mit den aktuellen
Dialogwerten, zeigt einen Beispielabruf und sendet nichts.

Der Offline-Modus lässt Diagnose, Fixes, Vorlagen, Patch-Validierung, Preflight
und lokale Wissenssuche vollständig verfügbar. Es wird kein Providerprozess
gestartet.

---

## 13. Datenschutz und Bedrohungsmodell

### Daten, die das Programm verlassen

Nur wenn der Nutzer **Explain last run**, **Send**, **AI suggest** oder
**Ask AI for proposal** klickt:

- der kompakte Projektdigest (nicht das vollständige Projekt-JSON),
- der gerenderte Run-Report, das gefilterte Log und optional der Skriptauszug,
- die deterministische Diagnose und die Nutzerfrage,
- die besten lokalen Wissensauszüge mit relativen Pfaden und Zeilenbereichen
  (nur wenn lokales Wissen aktiviert ist),
- bei der Generierung: Vorlagenkatalog/Schema oder Projektdigest und Anweisung.

Keine Projektdatei wird hochgeladen. Kein Arbeitsverzeichnis wird gemountet.
OpenCode läuft in einem neutralen temporären Verzeichnis mit deaktiviertem
`stdin`.

### Datenschutz der lokalen Wissenssuche

Indizierung, Tokenisierung, Ranking und Versionserkennung laufen im
VisualopenEMS-Prozess. Der Index wird nie auf die Festplatte geschrieben,
Quelldateien werden nie ausgeführt und für den Abruf findet kein Netzwerkzugriff
statt. Der Prompt enthält nur die ausgewählten Auszüge, die `[K#]`-Köpfe und die
Versionszeile. Alle Wurzeln und Treffer (auch durch ein knappes Zeichenbudget
entfernte Auszüge) sind lokal in **Sources…** sichtbar; die Prompt-Vorschau
zeigt exakt, was gesendet wird.

Zusätzliche Ordner (`ai_knowledge_paths`) können persönliche Notizen enthalten.
Sie werden nur indiziert, wenn der Nutzer lokales Wissen aktiviert und sie
hinzufügt. Wird ein Ordner entfernt und gespeichert, baut sich der Index ohne ihn
neu auf.

### Grenzen der Redaktion

Die Redaktion deckt das Home-Verzeichnis, gängige Token-Formate und
Bearer-/API-Key-Header ab. Sie ist kein allgemeines DLP-System. Absolute Pfade
außerhalb des Benutzerprofils, Hostnamen, Materialnamen oder Kommentare können
weiterhin sichtbar sein. Der Vorschaudialog ist die letzte Kontrolle vor dem
Senden.

### Hinweis zum externen Prozess

VisualopenEMS weist das Modell an, keine Tools zu verwenden und keine Dateien zu
lesen/schreiben. OpenCode ist aber ein separates Programm. Seine eigenen
Berechtigungen, Konfiguration und Sandbox bestimmen, was technisch möglich ist.
Für sensible Umgebungen:

- Offline-Modus verwenden,
- OpenCode-Konfiguration/Berechtigungen einschränken,
- den Prompt in **What AI sees** prüfen,
- die Anwendung in einem isolierten Konto oder einer VM betreiben.

### Schranken für Projektänderungen

Folgendes ist im Code erzwungen, nicht nur im Prompt:

- strikte Operations-/Feld-Allow-Listen,
- Typ- und Bereichsprüfungen,
- Projekt-JSON-Roundtrip,
- Validierung durch den Variablen-Resolver,
- Preflight-Fehler blockieren Apply/Create,
- Deep-Copy-Anwendung mit Alles-oder-nichts-Verhalten,
- expliziter Apply/Create-Button,
- Undo in einer Stufe,
- kein automatischer Mesh-Build, Solverlauf, Commit oder Speichern.

---

## 14. Tests und Verifikation

### Unit-Tests

```powershell
python -m pytest tests -q
```

Relevante Dateien:

- `tests/test_ai_assistant.py` — Diagnose-Codes, Safe-Fix-Validierung,
  Kontext-Digest/Redaktion, Prompt-Aufbau, OpenCode-Event-Parsing.
- `tests/test_ai_phase2.py` — jede Vorlage baut und besteht den Preflight;
  unbekannte Vorlagen/Parameter/Operationen/Felder werden abgelehnt; Patches
  werden auf einer Kopie angewendet; JSON-Extraktion aus Markdown;
  Port-/Material-Ergänzungen; Preflight-Befunde.
- `tests/test_ai_knowledge.py` — fokussierte Wurzeln, Extra-Pfade, sicherer Scan
  (Binär-/versteckte/Cache-/große Dateien), Chunking, BM25-ähnliches Ranking,
  Zeichenbudget, deaktivierter Modus, Cache-Invalidierung, Versionserkennung,
  deutsche Domain-Aliase, Prompt-Zitate und Kürzung.

Aktuelles Ergebnis: **531 passed**.

### GUI-Smoke-Tests (offscreen)

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python tests\ai_assistant_smoke.py
python tests\project_generator_smoke.py
python tests\menu_smoke.py
python tests\tab_help_smoke.py
```

Der AI-Smoke deckt Dock-Zustand, deterministische Diagnose, Fix-Apply/Undo,
lokalen Wissensabruf samt Zitat-/Sources-Status, deaktiviertes Wissen und einen
Mock-Worker ab. Der Generator-Smoke deckt Vorlagenkatalog, Patch-/Yagi-Vorschau,
Mock-KI-Vorschlag, Projektinstallation, strukturierten Patch, Preflight, Apply
und Undo sowie den Patch-Dialog mit Mock-Backend ab. `menu_smoke` prüft die
neuen AI-/Help-Aktionen; `tab_help_smoke` prüft, dass jedes Hilfethema existiert
und nicht trivial ist.

### Live-Verifikation

Der echte OpenCode-Pfad wurde geprüft mit:

- einem Vorlagenvorschlag für einen 2,45-GHz-FR4-Patch,
- einem strukturierten Patch mit `set_sim`, `set_port` und `set_variable`,
- einem anschließenden Preflight ohne Fehler,
- lokalem Abruf für PML-, `AddLumpedPort`- und NF2FF-Fragen mit sichtbaren
  **Sources…**-Einträgen und Versionserkennung (openEMS 0.0.36, CSXCAD 0.6.3,
  easyMesh 0.0.1, VisualopenEMS 0.12.0 auf dem Entwicklungsrechner).

Die Live-Verifikation hängt von der lokalen OpenCode-Installation und Anmeldung
ab und ist nicht Teil der automatisierten Testsuite.

---

## 15. Erweiterung des Systems

### Ein neues Backend

`AiBackend` implementieren:

```python
class MyBackend(AiBackend):
    name = "My backend"
    def is_available(self) -> bool: ...
    def run(self, prompt, **kwargs) -> AiReply: ...
    def cancel(self) -> None: ...
```

Eine `AiReply` mit `text` und optional `error`, `session_id`, `meta`
zurückgeben. Über `AiWorker` routen; die GUI nie aus dem Backend-Thread
aufrufen. Eine Provider-Auswahl in `AiSettingsDialog` und eine Factory in
`MainWindow`/den Generatordialogen ergänzen. Kontext-Builder und Validierung
unverändert lassen.

### Eine neue Vorlage

1. Eine reine Fabrik in `app/ai/templates.py` ergänzen (oder einen vorhandenen
   Generator aufrufen).
2. `TemplateParam`-Einträge mit realistischen Grenzen und Einheiten deklarieren.
3. Eine `ProjectTemplate` registrieren.
4. Die Vorlage in die Tests aufnehmen, damit sie bauen und den Preflight
   bestehen muss.
5. Annahmen dokumentieren; eine Vorlage ist ein Startpunkt, kein garantiertes
   Design.

### Eine neue Patch-Operation

1. Den Operationsnamen in `_ALLOWED_OPS` ergänzen.
2. Eine strikte Feld-Allow-List und Typprüfungen hinzufügen.
3. Nur auf der Deep Copy anwenden.
4. Preflight-Konsequenzen ergänzen, wenn das Feld das Modell brechen kann.
5. Tests für gültige und ungültige Payloads ergänzen.
6. Niemals freien Code, Shell-Befehle oder beliebige Dateipfade akzeptieren.

### Eine neue Diagnoseregel

Eine reine Abbildung von einem `RunReport`-Finding oder einer Projektbedingung
auf ein `DiagnosisIssue` ergänzen. Ist eine sichere Korrektur möglich, über
`make_change()`/`FixProposal` erzeugen und innerhalb der Allow-List bleiben. Einen
Test mit einem synthetischen Report ergänzen.

### Eine neue Wissenswurzel oder ein Alias

1. Einen `KnowledgeRoot`-Eintrag in `discover_roots()` mit enger Suffixliste,
   kurzem Anzeigenamen und Gewicht ergänzen. Fokussierte Unterordner sind einem
   kompletten Installationsbaum vorzuziehen.
2. Die Datei muss innerhalb der Scan-Limits bleiben, sonst die Limits prüfen.
3. `_TERM_ALIASES` nur für eine eindeutige technische Übersetzung ergänzen;
   generische Wörter verschmutzen den Index.
4. Einen Test mit temporärem Ordner ergänzen und prüfen, dass Pfade außerhalb
   der Wurzel, Binärdateien und Cache-Ordner übersprungen werden.
5. **Sources…** und das Prompt-Budget prüfen. Kein Netzwerkabruf und kein
   Dateischreiben in den Abrufpfad einbauen.

---

## 16. Grenzen und spätere Arbeiten

### Aktuelle Grenzen

- Der Assistent sieht einen kompakten Digest, keine CAD-Solids und kein
  vollständiges Mesh.
- Erklärungen sind probabilistisch; die deterministische Diagnose bleibt die
  maßgebliche Checkliste.
- Lokales Wissen ist lexikalisch, nicht semantisch. PDF, DOCX, Bilder und
  Binärdateien werden nicht indiziert, sehr neue Online-Wiki-Seiten nicht
  automatisch geladen. Deutsche Fragen werden über eine kleine Aliastabelle
  überbrückt, nicht über ein Übersetzungsmodell.
- Vorlagen sind ingenieurmäßige Startpunkte. Maße, Substrat und Matching müssen
  mit Mesh und Simulation verifiziert werden.
- Keine beliebige FreeCAD-/STEP-Generierung.
- Keine Mehrvariablen-Optimierung, kein Study-Objekt, keine autonome Run-Schleife
  und kein Parameter-Optimierer.
- Keine Bild-/Screenshot-Eingabe.
- OpenCode-Berechtigungen liegen außerhalb der Kontrolle von VisualopenEMS.

### Später geplant (nicht implementiert)

- Mehrvariablen-Optimierung mit einem Projektobjekt `StudySpec`.
- Ziel-/Nebenbedingungsdefinitionen aus S-Parametern und NF2FF-Ergebnissen.
- Fortsetzbare Study-Läufe mit Budget, Abbruch und Best-Design-Apply.
- Eine gemeinsame Tool-Schicht für weitere Backends (z. B. Copilot SDK).
- Optionale Vision-Unterstützung.

Das Review-first-Sicherheitsmodell (Vorschlag → Diff → Validierung → explizites
Apply) soll auch für diese Erweiterungen erhalten bleiben.
