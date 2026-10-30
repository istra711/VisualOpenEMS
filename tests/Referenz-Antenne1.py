import os
import numpy as np
from openEMS import openEMS
from CSXCAD import ContinuousStructure

# 1. Setup Simulation & Frequenzen
FDTD = openEMS()
# Frequenzbereich: 2.0 bis 3.0 GHz (Zentrum bei 2.45 GHz)
f_start = 2.0e9
f_stop  = 3.0e9
FDTD.SetContinuousWaveFilter(f_start, f_stop)

CSX = ContinuousStructure()
FDTD.SetCSX(CSX)

# 2. Geometrie-Parameter (in mm)
unit = 1e-3 # Basis-Einheit: Millimeter
CSX.SetUnit(unit)

# Substrat (FR4: eps_r = 4.4, Dicke = 1.6mm)
sub_W = 60.0   # Breite (X)
sub_L = 60.0   # Länge (Y)
sub_H = 1.6    # Höhe (Z)

# Patch (Ausgelegt für ca. 2.45 GHz)
patch_W = 38.0
patch_L = 29.5

# Mikrostreifen-Zuleitung (50 Ohm Breite auf FR4)
feed_W = 3.05
feed_L = (sub_L - patch_L) / 2

# 3. Materialien definieren
# Substrat
FR4 = CSX.AddMaterial('FR4')
FR4.SetPermittivity(4.4)

# Perfekter elektrischer Leiter (PEC) für Patch, Feed und Groundplane
PEC = CSX.AddMetal('PEC')

# 4. Geometrie zeichnen (Koordinatenursprung in der Mitte der Unterseite)
# Groundplane
PEC.AddBox(priority=10, start=[-sub_W/2, -sub_L/2, 0], end=[sub_W/2, sub_L/2, 0])

# Substrat
FR4.AddBox(priority=1, start=[-sub_W/2, -sub_L/2, 0], end=[sub_W/2, sub_L/2, sub_H])

# Patch (auf der Oberseite des Substrats)
PEC.AddBox(priority=10, start=[-patch_W/2, -patch_L/2, sub_H], end=[patch_W/2, patch_L/2, sub_H])

# Microstrip Feedline
feed_start_y = -sub_L/2
feed_end_y   = -patch_L/2
PEC.AddBox(priority=10, start=[-feed_W/2, feed_start_y, sub_H], end=[feed_W/2, feed_end_y, sub_H])

# 5. FDTD-Gitter (Mesh) & Randbedingungen (PML)
# Randbedingungen: Überall 8 Schichten PML (absorbierend)
FDTD.SetBoundaryCond(['PML_8', 'PML_8', 'PML_8', 'PML_8', 'PML_8', 'PML_8'])

# Einfaches, gleichmäßiges Gitter für den Testlauf (z.B. lambda/20)
# Für hochgenaue Validierung sollte das Gitter an Kanten verfeinert werden (Sub-Grid)
mesh = CSX.GetGrid()
mesh.set_fixed_mesh('x', np.arange(-40, 41, 1))
mesh.set_fixed_mesh('y', np.arange(-40, 41, 1))
mesh.set_fixed_mesh('z', np.arange(-5, 10, 0.2)) # Feine Z-Auflösung für das Substrat

# 6. Simulation vorbereiten
Sim_Path = 'patch_antenna_benchmark'
if not os.path.exists(Sim_Path):
    os.makedirs(Sim_Path)

FDTD.WriteStack(Sim_Path)
print(f"FDTD-Simulations-Stack erfolgreich in '{Sim_Path}' geschrieben.")
# FDTD.Run(Sim_Path) # Zum Starten der Berechnung auskommentieren
