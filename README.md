# I Got Next Court Digital Twin

A Python tool that turns a pickup basketball court listing into a 3D scene built with OpenUSD. It generates the court, hoops, and a basketball, animates a jump shot on the simulation timeline, and blocks export until a dry run validation pass succeeds. It runs on any laptop with the open source USD library, and it also runs inside NVIDIA Omniverse Kit.

## Why this project exists

I Got Next helps players find pickup courts. Every court is a row of data: size, number of hoops, rim height, surface. A digital twin turns that row into a scene a player could preview before driving across town, and a simulation team could reuse for training or analytics. The risk is bad data. A listing with a 9 meter rim should never become a 3D scene. This tool checks the data first, builds second, and exports last.

## System architecture

Each court goes through the same seven stages. A failure at any stage stops that court and the others keep going.

```
 Listing data (court_spec.json)
        |
 Stage 1: Validate the listing   sizes, hoop count, rim height, shot distance
        |
 Stage 2: Create the USD stage   Z up, meters, 24 ticks per second, 97 ticks
        |
 Stage 3: Build the court        floor, lines, key, backboards, poles, rims
        |
 Stage 4: Reference the ball     separate asset layer, composition reference
        |
 Stage 5: Animate the shot       one time sample per tick
        |
 Stage 6: Dry run validation     references, bounding boxes, collisions, make check
        |
 Stage 7: Export                 court USD file plus manifest.json
```

Key design decisions:

* **Data first.** The listing is validated before any 3D work starts.
* **Layered assets.** The ball lives in its own file and every court references it.
* **Pure function animation.** Ball position depends only on the tick number, so scrubbing and replay always match.
* **Validation gate.** The build happens in a temporary folder and only passing courts reach the output folder.
* **Traceable scenes.** Each scene stores the court_id, name, and city as custom metadata on the root prim.
* **Kit aware.** Inside Omniverse the script syncs the live timeline. Outside Omniverse it skips that step.

## Prerequisites

1. Windows, macOS, or Linux.
2. Python 3.10 or newer. Check with `python3 --version`.
3. Git. Check with `git --version`.
4. Optional for Omniverse: an NVIDIA RTX GPU, a current driver, and Omniverse USD Composer or a Kit based app.

## Installation

```bash
git clone <your repository URL>
cd igotnext_court_twin
python3 -m venv .venv
source .venv/bin/activate            # macOS and Linux
.venv\Scripts\activate               # Windows
python -m pip install -r requirements.txt
python -c "from pxr import Usd; print(Usd.GetVersion())"
```

You should see a version tuple such as `(0, 26, 11)`. A `ModuleNotFoundError` means the environment is not active, so repeat the activate step.

To use Omniverse, open the Script Editor, load `court_twin.py`, and run it. The `pxr` modules ship inside Kit.

## Run it

```bash
python court_twin.py --dryrun                  # validate every court, write nothing
python court_twin.py                           # export every court that passes
python court_twin.py --court gso_indoor_01     # one court only
```

Open any file in `output` with `usdview` or Omniverse USD Composer and press Play.

## Connect it to I Got Next

`court_spec.json` holds synthetic example listings shaped like a court record. To connect real data, export your courts table to the same fields:

* court_id, name, city
* indoor (true or false) and surface (hardwood or asphalt)
* length_m, width_m, hoops, rim_height_m, shot_distance_m

The tool writes `output/manifest.json` with each court_id, its status, any errors, and the USD file name. The app can read that file to show which courts have a preview and which listings need fixing.

## Code walkthrough

* `validate_listing` checks the raw data before any 3D work.
* `create_stage` sets axis, units, timeline range, and the metadata that links the scene to the listing.
* `build_court` generates floor, lines, painted keys, backboards, poles, and rims.
* `author_ball_asset` and `reference_ball` write the ball layer and pull it into the scene.
* `shot_plan` and `ball_position` map a tick to a ball position as a pure function.
* `animate_ball` writes 97 time samples and syncs the Kit timeline when present.
* `validate_scene` runs the reference, bounding box, collision, and make checks.
* `process_court` and `main` orchestrate the run and write the manifest.

## Verification and troubleshooting

An outside engineer can confirm everything works in four steps.

### 1. Run the dry run

```bash
python court_twin.py --dryrun
```

Expected summary:

```
gso_indoor_01      VALIDATED
gso_outdoor_02     VALIDATED
bad_example_99     FAILED
```

The third court fails on purpose. Its rim height is 9 meters, and the log says `LISTING ERROR: rim_height_m 9.0 outside 2.4 to 3.1`. This proves the data gate works.

### 2. Read the physics checks

For each good court the log shows:

```
Reference check: PASS
Collision sweep: minimum clearance 0.455 m at tick 60 (required 0.05 m)
Make check: 0.000 m from rim center at tick 60, limit 0.109 m, descending=True
```

The make check confirms the ball is inside the rim and moving downward when it reaches the hoop.

### 3. Inspect the exported scene

```bash
python court_twin.py --court gso_indoor_01
python - <<'PY'
from pxr import Usd, UsdGeom
stage = Usd.Stage.Open("output/gso_indoor_01.usda")
ball = stage.GetPrimAtPath("/World/Ball")
print("Ball composed:", stage.GetPrimAtPath("/World/Ball/Body").IsValid())
print("Time samples:", len(UsdGeom.Xformable(ball).GetTimeSamples()))
print("Linked listing:", stage.GetPrimAtPath("/World").GetCustomDataByKey("igotnext"))
PY
```

Expected: `Ball composed: True`, `Time samples: 97`, and the court details.

### 4. Prove the shot check can fail

```bash
python - <<'PY'
import court_twin as w
w.RIM_RADIUS = 0.1          # rim smaller than the ball
raise SystemExit(w.main(["--dryrun", "--court", "gso_outdoor_02"]))
PY
```

Expected: `VALIDATION ERROR: Shot misses the rim` and a nonzero exit code.

### Common problems

* `ModuleNotFoundError: pxr`. Cause: environment not active. Fix: activate it and reinstall requirements.
* `Unresolved reference`. Cause: the ball file moved. Fix: keep `basketball_asset.usda` beside the court files.
* Court appears without a ball in a viewer. Cause: only one file was copied. Fix: copy the court file and the ball asset together.
* A court shows FAILED. Cause: bad listing data or a layout the ball cannot clear. Fix: read the error lines above the summary and correct the listing.
* Nothing in `output`. Cause: you used `--dryrun`. Fix: run again without that flag.

## Extend it

* Add players as referenced assets and test player to player clearance.
* Add three point arcs and free throw circles.
* Add UsdPhysics rigid bodies so the shot runs under a physics engine in Omniverse.
* Run the dry run in CI so every listing change validates automatically.

## Notes

Student portfolio project. The listing data is synthetic. NVIDIA, Omniverse, and OpenUSD belong to their respective owners.
