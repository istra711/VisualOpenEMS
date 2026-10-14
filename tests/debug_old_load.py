import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models.project import Project
p = Project.load(r'C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\yagi_param_test\Yagi_Param_Test.fcsim.json')
for gf in p.geometry_files:
    mp = getattr(gf, 'mesh_path', '') or ''
    print('gf.path:', gf.path, 'exists:', Path(gf.path).exists() if gf.path else 'N/A')
    print('gf.mesh_path:', mp, 'exists:', Path(mp).exists() if mp else 'N/A')
print('primitives:', len(p.primitives or []))
for prim in (p.primitives or []):
    pp = prim.get('path', '')
    print(f"  {prim.get('name')}: path={pp} exists={Path(pp).exists() if pp else 'N/A'}")
print('sim.output_dir:', repr(p.sim.output_dir))
print('mesh.num_lines:', p.mesh.num_lines)
print('mesh.dirs:', p.mesh.dirs)
print('mesh.min_cellsize_z:', p.mesh.min_cellsize_z)
print('mesh.use_arc_detection:', p.mesh.use_arc_detection)
