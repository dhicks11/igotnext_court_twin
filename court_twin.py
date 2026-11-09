#!/usr/bin/env python3
"""I Got Next court digital twin builder (OpenUSD).

Turns a court listing (a row of data) into a 3D basketball court scene with a
time sampled jump shot, then validates it before export.

Pipeline per court:
    1. Validate the listing data itself (sane sizes, rim height, hoop count).
    2. Build a USD stage: floor, markings, backboards, rims, poles.
    3. Author a reusable basketball asset layer and reference it.
    4. Animate a shot with one time sample per timeline tick.
    5. Dry run gate: references, bounding boxes, make check, collision sweep.
    6. Export scene plus a manifest.json the app can read.

Usage:
    python court_twin.py --dryrun                 # validate every court, write nothing
    python court_twin.py                          # export every court that passes
    python court_twin.py --court gso_indoor_01    # one court only
"""

import argparse
import json
import logging
import math
import shutil
import sys
import tempfile
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

try:  # Only importable inside an NVIDIA Omniverse Kit runtime.
    import omni.timeline  # type: ignore
    IN_KIT = True
except ImportError:
    IN_KIT = False

log = logging.getLogger("court_twin")

# Timeline: ticks are the simulation clock. 24 ticks make one second.
FPS = 24.0
START_TICK, SHOT_END_TICK, END_TICK = 0, 60, 96   # release, ball at rim, ball on floor

# Regulation sizes in meters.
BALL_RADIUS = 0.12
RIM_RADIUS = 0.2286          # inner rim radius (45.7 cm diameter)
RELEASE_HEIGHT = 2.2         # ball height at release
ARC_BUMP = 1.3               # extra apex height above the straight line path
BACKBOARD = (0.05, 1.83, 1.07)   # thickness, width, height
BACKBOARD_BOTTOM = 2.90      # bottom edge height of the board
BOARD_FROM_BASELINE = 1.2    # board plane distance inside the baseline
RIM_FROM_BOARD = 0.6         # rim center distance from the board plane
MIN_CLEARANCE = 0.05         # ball must stay this far from board and pole

BALL_ASSET = "basketball_asset.usda"


# ---------------------------------------------------------------------------
# Listing data validation (stage zero: bad data never reaches the 3D build)
# ---------------------------------------------------------------------------
def validate_listing(c: dict) -> list:
    errors = []
    if not 10.0 <= c["length_m"] <= 30.0: errors.append(f"length_m {c['length_m']} outside 10 to 30")
    if not 10.0 <= c["width_m"] <= 20.0: errors.append(f"width_m {c['width_m']} outside 10 to 20")
    if c["hoops"] not in (1, 2): errors.append(f"hoops must be 1 or 2, got {c['hoops']}")
    if not 2.4 <= c["rim_height_m"] <= 3.1: errors.append(f"rim_height_m {c['rim_height_m']} outside 2.4 to 3.1")
    if not 3.0 <= c["shot_distance_m"] <= 7.0: errors.append(f"shot_distance_m {c['shot_distance_m']} outside 3 to 7")
    return errors


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def material(stage, name, color, roughness=0.6, metallic=0.0, opacity=1.0, root="/World/Looks"):
    path = f"{root}/{name}"
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, f"{path}/Shader")
    sh.CreateIdAttr("UsdPreviewSurface")
    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(color)
    sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mat


def box(stage, path, size, center, mat=None):
    """Unit cube scaled to real dimensions and moved to its center point."""
    cube = UsdGeom.Cube.Define(stage, path)
    cube.GetSizeAttr().Set(1.0)
    x = UsdGeom.Xformable(cube)
    x.AddTranslateOp().Set(Gf.Vec3d(*center))
    x.AddScaleOp().Set(Gf.Vec3f(*size))
    if mat:
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(mat)
    return cube


def strut(stage, path, p0, p1, radius, mat=None):
    """A thin cylinder running from point p0 to point p1 (used for rim and net strands)."""
    a, b = Gf.Vec3d(*p0), Gf.Vec3d(*p1)
    d = b - a
    cyl = UsdGeom.Cylinder.Define(stage, path)
    cyl.GetRadiusAttr().Set(radius)
    cyl.GetHeightAttr().Set(d.GetLength())
    x = UsdGeom.Xformable(cyl)
    x.AddTranslateOp().Set((a + b) / 2)
    x.AddOrientOp().Set(Gf.Quatf(Gf.Rotation(Gf.Vec3d(0, 0, 1), d.GetNormalized()).GetQuat()))
    if mat:
        UsdShade.MaterialBindingAPI.Apply(cyl.GetPrim()).Bind(mat)
    return cyl


def ring(stage, path, center, radius, width, mat, segments=32):
    """A circle of thin struts, used for the rim."""
    UsdGeom.Xform.Define(stage, path)
    for i in range(segments):
        a0, a1 = 2 * math.pi * i / segments, 2 * math.pi * (i + 1) / segments
        p0 = (center[0] + radius * math.cos(a0), center[1] + radius * math.sin(a0), center[2])
        p1 = (center[0] + radius * math.cos(a1), center[1] + radius * math.sin(a1), center[2])
        strut(stage, f"{path}/S{i}", p0, p1, width / 2, mat)


def net(stage, path, center, radius, mat, drop=0.4, bottom_radius=0.12, strands=16):
    """A simple tapered net hanging below the rim."""
    UsdGeom.Xform.Define(stage, path)
    for i in range(strands):
        a = 2 * math.pi * i / strands
        top = (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a), center[2])
        bot = (center[0] + bottom_radius * math.cos(a), center[1] + bottom_radius * math.sin(a), center[2] - drop)
        strut(stage, f"{path}/V{i}", top, bot, 0.006, mat)
    # One mid-height ring so the net reads as a mesh.
    mid_r, mid_z = (radius + bottom_radius) / 2, center[2] - drop / 2
    for i in range(strands):
        a0, a1 = 2 * math.pi * i / strands, 2 * math.pi * (i + 1) / strands
        strut(stage, f"{path}/H{i}",
              (center[0] + mid_r * math.cos(a0), center[1] + mid_r * math.sin(a0), mid_z),
              (center[0] + mid_r * math.cos(a1), center[1] + mid_r * math.sin(a1), mid_z), 0.006, mat)


def slab(stage, path, p0, p1, width, z, mat):
    """A thin floor marking from (x0, y0) to (x1, y1)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    cube = UsdGeom.Cube.Define(stage, path)
    cube.GetSizeAttr().Set(1.0)
    x = UsdGeom.Xformable(cube)
    x.AddTranslateOp().Set(Gf.Vec3d((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, z))
    x.AddRotateZOp().Set(math.degrees(math.atan2(dy, dx)))
    x.AddScaleOp().Set(Gf.Vec3f(math.hypot(dx, dy), width, 0.01))
    UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(mat)


def circle_marking(stage, path, cx, cy, r, mat, segments=40, width=0.05, z=0.012):
    UsdGeom.Xform.Define(stage, path)
    for k in range(segments):
        a0, a1 = 2 * math.pi * k / segments, 2 * math.pi * (k + 1) / segments
        slab(stage, f"{path}/S{k}", (cx + r * math.cos(a0), cy + r * math.sin(a0)),
             (cx + r * math.cos(a1), cy + r * math.sin(a1)), width, z, mat)


def three_point_line(stage, path, rim, s, L, W, mat, radius=6.75, corner_margin=0.9, width=0.05, z=0.012):
    """Arc around the hoop plus straight corner lines running to the baseline."""
    UsdGeom.Xform.Define(stage, path)
    limit = W / 2 - corner_margin                  # arc may not get closer than this to a sideline
    th = math.asin(min(1.0, limit / radius))
    n = 40
    pts = [(rim[0] - s * radius * math.cos(-th + 2 * th * k / n), radius * math.sin(-th + 2 * th * k / n))
           for k in range(n + 1)]
    for k in range(n):
        slab(stage, f"{path}/A{k}", pts[k], pts[k + 1], width, z, mat)
    baseline = s * L / 2
    slab(stage, f"{path}/CornerA", pts[0], (baseline, pts[0][1]), width, z, mat)
    slab(stage, f"{path}/CornerB", pts[-1], (baseline, pts[-1][1]), width, z, mat)


def hoop_geometry(c: dict) -> list:
    """Return per hoop numbers: side sign, rim center, board center, pole center."""
    half = c["length_m"] / 2
    sides = [1] if c["hoops"] == 1 else [1, -1]
    hoops = []
    for s in sides:
        board_x = s * (half - BOARD_FROM_BASELINE)
        rim_x = s * (half - BOARD_FROM_BASELINE - RIM_FROM_BOARD)
        hoops.append({"sign": s, "board_x": board_x, "rim": Gf.Vec3d(rim_x, 0.0, c["rim_height_m"]),
                      "pole_x": s * (half + 0.3)})
    return hoops


# ---------------------------------------------------------------------------
# Stage construction
# ---------------------------------------------------------------------------
def create_stage(anchor: str, c: dict) -> Usd.Stage:
    stage = Usd.Stage.CreateNew(anchor)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetStartTimeCode(START_TICK)
    stage.SetEndTimeCode(END_TICK)
    stage.SetTimeCodesPerSecond(FPS)
    stage.SetFramesPerSecond(FPS)
    root = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(root.GetPrim())
    # Custom metadata ties the 3D scene back to the listing it came from.
    root.GetPrim().SetCustomDataByKey("igotnext", {"court_id": c["court_id"], "name": c["name"], "city": c["city"]})
    stage.GetRootLayer().documentation = f"Digital twin of court {c['court_id']}."
    return stage


def build_court(stage, c: dict) -> None:
    UsdGeom.Scope.Define(stage, "/World/Looks")
    hardwood = c["surface"] == "hardwood"
    floor_color = Gf.Vec3f(0.80, 0.58, 0.32) if hardwood else Gf.Vec3f(0.30, 0.30, 0.33)
    floor = material(stage, "Floor", floor_color, roughness=0.25 if hardwood else 0.9)
    apron = material(stage, "Apron", Gf.Vec3f(0.36, 0.20, 0.62), roughness=0.5)
    paint = material(stage, "Paint", Gf.Vec3f(0.46, 0.22, 0.72), roughness=0.35)
    line = material(stage, "Line", Gf.Vec3f(0.97, 0.97, 0.97), roughness=0.4)
    steel = material(stage, "Steel", Gf.Vec3f(0.70, 0.72, 0.76), roughness=0.4, metallic=0.3)
    glass = material(stage, "Board", Gf.Vec3f(0.85, 0.92, 0.98), roughness=0.05, opacity=0.45)
    rim_mat = material(stage, "Rim", Gf.Vec3f(1.0, 0.3, 0.05), roughness=0.4, metallic=0.6)
    net_mat = material(stage, "Net", Gf.Vec3f(0.96, 0.96, 0.96), roughness=0.9)

    L, W = c["length_m"], c["width_m"]
    # Dark purple out-of-bounds border under a glossy playing surface.
    box(stage, "/World/Floor", (L + 2.0, W + 2.0, 0.1), (0, 0, -0.05), apron)
    box(stage, "/World/PlayingSurface", (L, W, 0.004), (0, 0, 0.002), floor)

    # Boundary lines as thin slabs just above the floor (simplified markings).
    t, z = 0.05, 0.005
    UsdGeom.Xform.Define(stage, "/World/Lines")
    box(stage, "/World/Lines/SideA", (L, t, 0.01), (0, W / 2, z), line)
    box(stage, "/World/Lines/SideB", (L, t, 0.01), (0, -W / 2, z), line)
    box(stage, "/World/Lines/EndA", (t, W, 0.01), (L / 2, 0, z), line)
    box(stage, "/World/Lines/EndB", (t, W, 0.01), (-L / 2, 0, z), line)
    if c["hoops"] == 2:
        box(stage, "/World/Lines/Center", (t, W, 0.01), (0, 0, z), line)

    UsdGeom.Xform.Define(stage, "/World/Hoops")
    for i, h in enumerate(hoop_geometry(c)):
        s = h["sign"]
        # Painted key from baseline toward the court (5.8 m long, 4.9 m wide).
        box(stage, f"/World/Lines/Key{i}", (5.8, 4.9, 0.01), (s * (L / 2 - 2.9), 0, z * 2), paint)
        base = f"/World/Hoops/Hoop{i}"
        UsdGeom.Xform.Define(stage, base)
        box(stage, f"{base}/Backboard", BACKBOARD, (h["board_x"], 0, BACKBOARD_BOTTOM + BACKBOARD[2] / 2), glass)
        pole = UsdGeom.Cylinder.Define(stage, f"{base}/Pole")
        pole.GetRadiusAttr().Set(0.1)
        pole.GetHeightAttr().Set(BACKBOARD_BOTTOM + BACKBOARD[2])
        UsdGeom.Xformable(pole).AddTranslateOp().Set(Gf.Vec3d(h["pole_x"], 0, (BACKBOARD_BOTTOM + BACKBOARD[2]) / 2))
        UsdShade.MaterialBindingAPI.Apply(pole.GetPrim()).Bind(steel)
        ring(stage, f"{base}/Rim", h["rim"], RIM_RADIUS, 0.04, rim_mat)
        net(stage, f"{base}/Net", h["rim"], RIM_RADIUS, net_mat)
        # Court markings for this end: three point line and free throw circle.
        three_point_line(stage, f"/World/Lines/ThreePoint{i}", h["rim"], s, L, W, line)
        circle_marking(stage, f"/World/Lines/FreeThrow{i}", s * (L / 2 - 5.8), 0.0, 1.8, line)
    if c["hoops"] == 2:
        circle_marking(stage, "/World/Lines/CenterCircle", 0.0, 0.0, 1.8, line)
    log.info("Court built: %.1f m by %.1f m, %d hoop(s), rim %.2f m", L, W, c["hoops"], c["rim_height_m"])


def author_ball_asset(path: Path) -> None:
    """Write the basketball as its own reusable layer, referenced by every court."""
    asset = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(asset, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(asset, 1.0)
    root = UsdGeom.Xform.Define(asset, "/Ball")
    asset.SetDefaultPrim(root.GetPrim())
    sphere = UsdGeom.Sphere.Define(asset, "/Ball/Body")
    sphere.GetRadiusAttr().Set(BALL_RADIUS)
    sphere.GetDisplayColorAttr().Set([Gf.Vec3f(0.95, 0.42, 0.08)])
    UsdGeom.Scope.Define(asset, "/Ball/Looks")
    leather = material(asset, "Leather", Gf.Vec3f(0.95, 0.42, 0.08), roughness=0.85, root="/Ball/Looks")
    UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(leather)
    asset.GetRootLayer().Save()


def reference_ball(stage) -> None:
    ball = UsdGeom.Xform.Define(stage, "/World/Ball")
    ball.GetPrim().GetReferences().AddReference(f"./{BALL_ASSET}")


# ---------------------------------------------------------------------------
# Timeline bound animation
# ---------------------------------------------------------------------------
def shot_plan(c: dict) -> dict:
    """Pick the target hoop and the release spot from the listing."""
    hoop = hoop_geometry(c)[0]
    rim = hoop["rim"]
    # Release spot sits shot_distance_m from the rim, out toward center court.
    start = Gf.Vec3d(rim[0] - hoop["sign"] * c["shot_distance_m"], 0.0, RELEASE_HEIGHT)
    return {"hoop": hoop, "rim": rim, "start": start}


def ball_position(tick: float, plan: dict) -> Gf.Vec3d:
    """Pure function: timeline tick in, ball position out.

    Phase 1 (ticks 0 to 60): arc from the release spot to the rim center.
    Phase 2 (ticks 60 to 96): drop straight through the net to the floor.
    Computing from the tick, not a running counter, keeps scrubbing exact.
    """
    start, rim = plan["start"], plan["rim"]
    if tick <= SHOT_END_TICK:
        s = (tick - START_TICK) / (SHOT_END_TICK - START_TICK)
        x = start[0] + (rim[0] - start[0]) * s
        z = start[2] + (rim[2] - start[2]) * s + 4 * ARC_BUMP * s * (1 - s)
        return Gf.Vec3d(x, 0.0, z)
    f = (tick - SHOT_END_TICK) / (END_TICK - SHOT_END_TICK)
    f = f * f  # accelerate downward like gravity
    z = rim[2] + (BALL_RADIUS - rim[2]) * f
    return Gf.Vec3d(rim[0], 0.0, z)


def animate_ball(stage, plan) -> int:
    op = UsdGeom.Xformable(stage.GetPrimAtPath("/World/Ball")).AddTranslateOp()
    for tick in range(START_TICK, END_TICK + 1):
        op.Set(ball_position(tick, plan), Usd.TimeCode(tick))  # one sample per tick
    if IN_KIT:
        tl = omni.timeline.get_timeline_interface()
        tl.set_start_time(START_TICK / FPS)
        tl.set_end_time(END_TICK / FPS)
        tl.set_time_codes_per_second(FPS)
    return END_TICK - START_TICK + 1


# ---------------------------------------------------------------------------
# Dry run validation
# ---------------------------------------------------------------------------
def rng(cache, prim):
    return cache.ComputeWorldBound(prim).ComputeAlignedRange()


def gap(a, b):
    """Separation between two boxes. Negative means overlap."""
    return max(max(a.GetMin()[i] - b.GetMax()[i], b.GetMin()[i] - a.GetMax()[i]) for i in range(3))


def validate_scene(stage, plan) -> list:
    problems = []
    # 1. References resolve and compose to real content.
    for prim in stage.Traverse():
        for spec in prim.GetPrimStack():
            for ref in spec.referenceList.GetAddedOrExplicitItems():
                if ref.assetPath and Sdf.Layer.FindRelativeToLayer(stage.GetRootLayer(), ref.assetPath) is None:
                    problems.append(f"Unresolved reference {ref.assetPath} on {prim.GetPath()}")
        if prim.HasAuthoredReferences() and not prim.GetChildren():
            problems.append(f"Reference on {prim.GetPath()} composed to an empty prim")
    log.info("Reference check: %s", "PASS" if not problems else "FAIL")
    if problems:
        return problems

    # 2. Bounding boxes of the obstacles the ball must avoid.
    purposes = [UsdGeom.Tokens.default_]
    static = UsdGeom.BBoxCache(Usd.TimeCode.Default(), purposes)
    obstacles = []
    for prim in stage.Traverse():
        if prim.GetName() in ("Backboard", "Pole"):
            r = rng(static, prim)
            obstacles.append((prim.GetPath(), r))
            log.info("BBox %-28s min=%s max=%s", prim.GetPath(), tuple(round(v, 2) for v in r.GetMin()), tuple(round(v, 2) for v in r.GetMax()))

    # 3. Collision sweep: ball bounding box vs every obstacle at every tick.
    ball = stage.GetPrimAtPath("/World/Ball")
    min_gap, worst = float("inf"), START_TICK
    for tick in range(START_TICK, END_TICK + 1):
        cache = UsdGeom.BBoxCache(Usd.TimeCode(tick), purposes)
        br = rng(cache, ball)
        for _, r in obstacles:
            g = gap(br, r)
            if g < min_gap:
                min_gap, worst = g, tick
    log.info("Collision sweep: minimum clearance %.3f m at tick %d (required %.2f m)", min_gap, worst, MIN_CLEARANCE)
    if min_gap < MIN_CLEARANCE:
        problems.append(f"Ball clearance {min_gap:.3f} m below {MIN_CLEARANCE} m at tick {worst}")

    # 4. Make check: ball center must be inside the rim, moving downward.
    cache = UsdGeom.BBoxCache(Usd.TimeCode(SHOT_END_TICK), purposes)
    c_now = rng(cache, ball).GetMidpoint()
    prev = rng(UsdGeom.BBoxCache(Usd.TimeCode(SHOT_END_TICK - 1), purposes), ball).GetMidpoint()
    rim = plan["rim"]
    off = math.hypot(c_now[0] - rim[0], c_now[1] - rim[1])
    log.info("Make check: %.3f m from rim center at tick %d, limit %.3f m, descending=%s",
             off, SHOT_END_TICK, RIM_RADIUS - BALL_RADIUS, prev[2] > c_now[2])
    if off > RIM_RADIUS - BALL_RADIUS:
        problems.append("Shot misses the rim")
    if not prev[2] > c_now[2]:
        problems.append("Ball is not descending at the rim")
    return problems


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def process_court(c: dict, out_dir: Path, dryrun: bool) -> dict:
    result = {"court_id": c["court_id"], "name": c["name"], "city": c["city"], "status": "failed", "errors": []}
    log.info("==== COURT %s: %s ====", c["court_id"], c["name"])

    errors = validate_listing(c)
    for e in errors:
        log.error("LISTING ERROR: %s", e)
    if errors:
        result["errors"] = errors
        return result

    with tempfile.TemporaryDirectory(prefix="court_twin_") as tmp:
        work = Path(tmp)
        author_ball_asset(work / BALL_ASSET)
        scene_name = f"{c['court_id']}.usda"
        stage = create_stage(str(work / scene_name), c)
        build_court(stage, c)
        reference_ball(stage)
        plan = shot_plan(c)
        n = animate_ball(stage, plan)
        log.info("Animation authored: %d time samples", n)

        problems = validate_scene(stage, plan)
        for p in problems:
            log.error("VALIDATION ERROR: %s", p)
        if problems:
            result["errors"] = problems
            return result

        result["status"] = "validated" if dryrun else "exported"
        if not dryrun:
            stage.GetRootLayer().Save()
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / scene_name, out_dir / scene_name)
            shutil.copy2(work / BALL_ASSET, out_dir / BALL_ASSET)
            result["usd_file"] = scene_name
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build and validate court digital twins.")
    ap.add_argument("--spec", default="court_spec.json")
    ap.add_argument("--court", help="only this court_id")
    ap.add_argument("--dryrun", action="store_true", help="validate only, export nothing")
    ap.add_argument("--out", default="output")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    courts = json.loads(Path(args.spec).read_text())["courts"]
    if args.court:
        courts = [c for c in courts if c["court_id"] == args.court]
        if not courts:
            log.error("No court with id %s", args.court)
            return 2

    results = [process_court(c, Path(args.out).resolve(), args.dryrun) for c in courts]

    log.info("==== SUMMARY ====")
    for r in results:
        log.info("%-18s %s", r["court_id"], r["status"].upper())
    if not args.dryrun and any(r["status"] == "exported" for r in results):
        out = Path(args.out).resolve()
        (out / "manifest.json").write_text(json.dumps({"fps": FPS, "courts": results}, indent=2))
        log.info("Manifest written: %s", out / "manifest.json")
    return 0 if all(r["status"] != "failed" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
