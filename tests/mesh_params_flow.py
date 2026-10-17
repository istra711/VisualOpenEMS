import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project import MeshSpec, BoundarySpec
from app.meshing.rules import compute_targets, air_margin_units

# Test that global_setup delivers all keys correctly
m = MeshSpec(
    preset='high', drawing_unit='mm',
    start_frequency=2.2e9, stop_frequency=2.7e9,
    refined_cellsize=None, min_cellsize=0.1, max_cellsize=5.0,
    min_cellsize_z=0.05, smooth_metal_edge='extra_lines',
    use_circle_detection=True, use_arc_detection=True,
    handle_closely_placed_edges=False, max_grading_ratio=1.5,
    num_lines=8, dirs='xy',
    add_graded_mesh_lines_at_material_transitions=True,
)
b = BoundarySpec(air_margin=['auto']*6)
setup = m.global_setup(b)

# Verify all keys present
expected = {
    'min_cellsize': 0.1,
    'min_cellsize_z': 0.05,
    'max_cellsize': 5.0,
    'num_lines': 8,
    'dirs': 'xy',
    'use_arc_detection': True,
    'smooth_metal_edge': 'extra_lines',
    'handle_closely_placed_edges': False,
    'use_circle_detection': True,
    'max_grading_ratio': 1.5,
}
for key, want in expected.items():
    got = setup.get(key, 'MISSING')
    status = 'OK' if got == want else f'FAIL (got {got!r}, want {want!r})'
    print(f'  {key}: {status}')

# The metal-interface refinement is applied APP-SIDE after easyMesh
# (app/meshing/interface_refine.py) — the flag must NOT be forwarded, because
# easyMesh's internal pass is smoothed away and misses metal-in-air.
not_forwarded = 'add_graded_mesh_lines_at_material_transitions' not in setup
print(
    "  add_graded_mesh_lines_at_material_transitions not forwarded to easyMesh: "
    + ('OK' if not_forwarded else 'FAIL (still forwarded)')
)
assert not_forwarded, 'app-side refinement flag must not reach easyMesh'

# Simulate what easyMesh does in get_mesh_parameters (after fix)
print('\n--- Simulating easyMesh get_mesh_parameters ---')
wave_length = 3e8 / 2.7e9  # fstop
mesh_res = wave_length / 20  # high preset

# OLD behavior:
old_num_lines = setup.get('num_lines', 5) + 2
old_min_cellsize = mesh_res / 4  # hardcoded

# NEW behavior (after fix):
new_num_lines = setup.get('num_lines', None) or 5
new_min_cellsize = setup.get('min_cellsize', mesh_res / 4)

print(f'  OLD num_lines: {old_num_lines} (user wanted 8)')
print(f'  NEW num_lines: {new_num_lines} (user wanted 8)')
print(f'  OLD min_cellsize: {old_min_cellsize:.4f} (user wanted 0.1)')
print(f'  NEW min_cellsize: {new_min_cellsize:.4f} (user wanted 0.1)')
print(f'  min_cellsize_z from setup: {setup.get("min_cellsize_z")}')

assert new_num_lines == 8, f'num_lines FAIL: {new_num_lines}'
assert abs(new_min_cellsize - 0.1) < 1e-9, f'min_cellsize FAIL: {new_min_cellsize}'
assert setup.get('min_cellsize_z') == 0.05, f'min_cellsize_z FAIL'
print('\nALL PASS')
