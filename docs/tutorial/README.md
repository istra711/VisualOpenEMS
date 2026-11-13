# Inverted-F Antenna on a PCB - 5.8 GHz tutorial

[Watch the MP4](IFA_5p8GHz_Tutorial.mp4)

![Poster](frames/01.png)

A step-by-step VisualOpenEMS walkthrough: build the PCB and the
inverted-F metal, add the 50 ohm port, mesh the model, run openEMS and
read the S-parameters, the 3D antenna pattern and the E-field.

> The GUI screenshots are real. The S-parameter curve, pattern and
> field maps use representative IFA data so the viewers can be explained
> without a long FDTD run; your own results will differ with geometry,
> mesh and materials.

## Geometry

| Part | Dimensions / position |
| --- | --- |
| FR4 substrate | 25 x 45 x 1.6 mm, eps_r = 4.4, tan_d = 0.02 |
| Bottom ground | 25 x 45 mm, z = 0...0.035 mm |
| Top ground | 25 x 35 mm, z = 1.6...1.635 mm |
| IFA arm | 7 x 2 mm, y = 38...40 mm, z = 1.6...1.635 mm |
| Short | 2 x 5 mm, x = 10...12 mm, y = 35...40 mm |
| Feed strip | 2 x 2.5 mm, x = 12.5...14.5 mm, y = 35.5...38 mm |
| Port | lumped, 50 ohm, direction y across the 0.5 mm gap, width x = 2 mm |
| Mesh | max 1.2 mm, min 0.15 mm, 18 cells/lambda at 5.8 GHz |

## Steps

### Step 1 - Set up the project

New Project. Set the drawing unit to **mm** and the frequency range to **5.0-6.6 GHz**.
The target frequency is **5.8 GHz**.

- Start/Stop must cover the resonance, so leave some margin on both sides.
- The target frequency drives the mesh density and the NF2FF evaluation.
- Check the computed block: max cell 1.2 mm, air margin 11.36 mm.

![Step 1](assets/dialog_sim_params.png)

### Step 2 - Build the stackup

Create the FR4 material and the substrate box.

- FR4: dielectric, eps_r = 4.4, tan_d = 0.02.
- PCB box: 25 x 45 x 1.6 mm.
- Bottom ground: the full board, 0.035 mm copper.
- Top ground: y = 0...35 mm; leave a 10 mm antenna clearance.

![Step 2](assets/dialog_material.png)

![Step 2](assets/dialog_box_pcb.png)

### Step 3 - Draw the IFA

Place the three copper boxes of the inverted-F.

- Arm: 7 x 2 mm at y = 38...40 mm.
- Short: 2 x 5 mm at x = 10...12 mm.
- Feed: 2 x 2.5 mm at x = 12.5...14.5 mm.
- Keep a 0.5 mm gap between the feed and the top ground.

![Step 3](assets/layout_diagram.png)

![Step 3](assets/dialog_box_arm.png)

### Step 4 - Add the feed port

Add a lumped port across the 0.5 mm gap.

- Type: lumped; resistance 50 ohm; direction y.
- Width (lumped): **Area - as wide as the metal (x)**.
- Start 35.5 mm, stop 35.0 mm; the metal reference is `IFA_feed`.
- The port list marks it as `area along X`.

![Step 4](assets/dialog_port.png)

### Step 5 - Mesh

Resolve the feed gap and the copper edges.

- lambda0 = 51.7 mm at 5.8 GHz; 18 cells per wavelength.
- Max cell 1.2 mm, min cell 0.15 mm, min z cell 0.08 mm.
- Check the XY slice and the Cell sizes tab; the gap and the metal
  edges must contain enough grid lines.

![Step 5](assets/dialog_mesh_params.png)

![Step 5](assets/mesh_viewer_xy.png)

### Step 6 - Request the results

Add the field dump and the far-field box.

- Dump: quantity E, 5.8 GHz, VTK, box around the antenna.
- NF2FF: enable, frequency list 5.8 GHz, theta = 181, phi = 361.
- The dump feeds the Field Viewer; the NF2FF feeds the Antenna tab.

![Step 6](assets/dialog_dump.png)

![Step 6](assets/dialog_nf2ff.png)

### Step 7 - Run

Run Simulation (Ctrl+R).

- Watch the TS and energy bars; openEMS stops at the energy criterion.
- The log lists the S-parameter and NF2FF output files.
- Typical runtime for this model is a few minutes on a laptop.

![Step 7](assets/main_run_crop.png)

### Step 8 - S-parameters

Open the S-Params tab and plot `|S| dB`.

- The marker at 5.8 GHz shows |S11| = -28.17 dB and VSWR 1.08.
- The -10 dB band is about 5.65-5.95 GHz.
- Smith, VSWR, phase and Re/Im plots are in the same tab.

![Step 8](assets/sparam_viewer.png)

### Step 9 - Antenna pattern

Open the Antenna tab with the NF2FF result.

- 3D gain at 5.8 GHz: max 2.6 dBi, efficiency 68 %.
- Use the E-plane (phi = 90 deg) and H-plane (phi = 0 deg) cuts.
- Markers read gain, angle and HPBW; the nulls show the board influence.

![Step 9](assets/antenna_3d.png)

### Step 10 - E-field

Open the Field Viewer and select `pt:magnitude`.

- XY slice at z = 1.6 mm shows the copper/field distribution.
- YZ slice at x = 13.5 mm shows the field across the feed gap.
- The hot spot sits at the gap and along the arm; use the slider to animate.

![Step 10](assets/field_viewer.png)

## Reading the result panels

**S-parameters.** `|S| dB` plots the magnitude of S11 in dB. The
minimum is the resonance; the -10 dB points give the usable band. The
same tab offers Smith, VSWR, phase and Re/Im plots.

**Antenna pattern.** The 3D view colours gain in dBi. E-plane
(phi = 90 deg) and H-plane (phi = 0 deg) cuts show the beamwidth; the
markers read gain, angle and HPBW. Efficiency is radiated power divided
by accepted power.

**E-field.** The Field Viewer loads the VTK dump. The XY slice at
z = 1.6 mm shows the current/field distribution on the copper; the YZ
slice through the feed shows the field across the 0.5 mm gap and the
substrate. Use the time or frequency slider to animate a series.

## Reproduce the assets

```bash
python docs/tutorial/generate_tutorial.py
```

This regenerates the screenshots, the twelve slides (in `frames/`) and
the MP4. Install `ffmpeg` for the video step; the PNG frames are
written even without it.

## Files

- `IFA_5p8GHz_Tutorial.mp4` - 54 s, 1600 x 900, H.264, no audio.
- `frames/01.png` ... `frames/12.png` - the twelve slides.
- `assets/` - real GUI screenshots and the rendered diagrams/field maps.
- `generate_tutorial.py` - regenerates all of the above.

## Notes

- The geometry is a starting point, not a tuned production antenna.
  Expect the resonance to move with substrate tolerances and the feed
  detail; tune the arm length and the feed offset on your own board.
- Increase `end_criteria` or `max_timesteps` for a smoother S-parameter
  curve; a finer mesh costs runtime.
- The Field Viewer can also animate a time series and overlay vectors
  or streamlines; the same data can be opened in ParaView.
