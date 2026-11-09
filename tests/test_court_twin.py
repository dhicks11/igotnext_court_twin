"""Tests for the court digital twin pipeline.

Run from the project folder:  python -m pytest
"""
import json
from pathlib import Path

import pytest
from pxr import Usd, UsdGeom

import court_twin as ct

SPEC_PATH = Path(__file__).resolve().parent.parent / "court_spec.json"
SPEC = json.loads(SPEC_PATH.read_text())["courts"]
GOOD = [c for c in SPEC if not c["court_id"].startswith("bad_example")]
BAD = [c for c in SPEC if c["court_id"].startswith("bad_example")]


def court(court_id):
    return next(dict(c) for c in SPEC if c["court_id"] == court_id)


# ---------------------------------------------------------------------------
# Stage 1: listing validation (bad data must never reach the 3D build)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("c", GOOD, ids=lambda c: c["court_id"])
def test_good_listings_pass_validation(c):
    assert ct.validate_listing(c) == []


@pytest.mark.parametrize("c", BAD, ids=lambda c: c["court_id"])
def test_bad_listings_fail_validation(c):
    assert ct.validate_listing(c)


@pytest.mark.parametrize("field, value", [
    ("length_m", 5.0), ("length_m", 40.0),
    ("width_m", 5.0), ("width_m", 25.0),
    ("hoops", 0), ("hoops", 3),
    ("rim_height_m", 2.0), ("rim_height_m", 9.0),
    ("shot_distance_m", 2.0), ("shot_distance_m", 8.0),
])
def test_out_of_range_value_gives_exactly_one_error_naming_the_field(field, value):
    c = court("gso_indoor_01")
    c[field] = value
    errors = ct.validate_listing(c)
    assert len(errors) == 1
    assert field in errors[0]


@pytest.mark.parametrize("field, value", [
    ("length_m", 10.0), ("length_m", 30.0),
    ("width_m", 10.0), ("width_m", 20.0),
    ("rim_height_m", 2.4), ("rim_height_m", 3.1),
    ("shot_distance_m", 3.0), ("shot_distance_m", 7.0),
])
def test_range_boundaries_are_allowed(field, value):
    c = court("gso_indoor_01")
    c[field] = value
    assert ct.validate_listing(c) == []


@pytest.mark.parametrize("field, value", [
    ("length_m", 9.99), ("length_m", 30.01),
    ("width_m", 9.99), ("width_m", 20.01),
    ("rim_height_m", 2.39), ("rim_height_m", 3.11),
    ("shot_distance_m", 2.99), ("shot_distance_m", 7.01),
])
def test_values_just_outside_the_range_are_rejected(field, value):
    # Pins each limit tightly, so loosening one (say rim 3.1 to 4.0) is caught.
    c = court("gso_indoor_01")
    c[field] = value
    assert len(ct.validate_listing(c)) == 1


def test_every_problem_is_reported_not_just_the_first():
    c = court("gso_indoor_01")
    c.update(rim_height_m=9.0, hoops=3)
    assert len(ct.validate_listing(c)) == 2


def test_spec_themes_exist():
    for c in SPEC:
        assert c.get("theme", "purple") in ct.THEMES


# ---------------------------------------------------------------------------
# Hoop placement
# ---------------------------------------------------------------------------
def test_half_court_has_one_hoop_on_the_positive_side():
    hoops = ct.hoop_geometry(court("gso_outdoor_02"))
    assert [h["sign"] for h in hoops] == [1]


def test_full_court_hoops_mirror_each_other():
    a, b = ct.hoop_geometry(court("gso_indoor_01"))
    assert a["sign"] == 1 and b["sign"] == -1
    assert a["rim"][0] == pytest.approx(-b["rim"][0])


def test_rim_sits_at_the_listed_height_and_distance_from_baseline():
    c = court("gso_indoor_01")
    rim = ct.hoop_geometry(c)[0]["rim"]
    assert rim[2] == c["rim_height_m"]
    assert rim[0] == pytest.approx(c["length_m"] / 2 - ct.BOARD_FROM_BASELINE - ct.RIM_FROM_BOARD)


# ---------------------------------------------------------------------------
# Animation: the ball position is a pure function of the tick
# ---------------------------------------------------------------------------
@pytest.fixture
def plan():
    return ct.shot_plan(court("gso_indoor_01"))


def test_timeline_matches_the_documented_contract():
    # Written as plain numbers on purpose: the README and the exported files promise
    # 24 ticks a second, release at 0, ball at the rim at 60, floor at 96 (97 samples).
    assert ct.FPS == 24.0
    assert (ct.START_TICK, ct.SHOT_END_TICK, ct.END_TICK) == (0, 60, 96)


def test_ball_starts_at_the_release_spot(plan):
    p = ct.ball_position(ct.START_TICK, plan)
    assert p[0] == pytest.approx(plan["start"][0])
    assert p[2] == pytest.approx(ct.RELEASE_HEIGHT)


def test_ball_is_at_the_rim_center_when_the_shot_ends(plan):
    p = ct.ball_position(ct.SHOT_END_TICK, plan)
    assert p == pytest.approx(plan["rim"], abs=1e-9)


def test_ball_ends_on_the_floor_under_the_rim(plan):
    p = ct.ball_position(ct.END_TICK, plan)
    assert p[0] == pytest.approx(plan["rim"][0])
    assert p[2] == pytest.approx(ct.BALL_RADIUS)


def test_same_tick_always_gives_the_same_position_in_any_order(plan):
    first = ct.ball_position(80, plan)
    ct.ball_position(10, plan)
    ct.ball_position(60, plan)
    assert ct.ball_position(80, plan) == first


def test_shot_arcs_above_both_the_release_height_and_the_rim(plan):
    apex = max(ct.ball_position(t, plan)[2] for t in range(ct.START_TICK, ct.SHOT_END_TICK + 1))
    assert apex > max(plan["start"][2], plan["rim"][2])


def test_ball_only_falls_after_it_reaches_the_rim(plan):
    heights = [ct.ball_position(t, plan)[2] for t in range(ct.SHOT_END_TICK, ct.END_TICK + 1)]
    assert all(a > b for a, b in zip(heights, heights[1:]))


def test_ball_stays_on_the_court_centerline(plan):
    assert all(ct.ball_position(t, plan)[1] == 0.0 for t in range(ct.START_TICK, ct.END_TICK + 1))


# ---------------------------------------------------------------------------
# Scene building
# ---------------------------------------------------------------------------
def build(c, tmp_path):
    stage = ct.create_stage(str(tmp_path / "scene.usda"), c)
    ct.build_court(stage, c)
    return stage


@pytest.mark.parametrize("court_id", ["gso_indoor_01", "gso_outdoor_02"])
def test_each_hoop_has_a_backboard_and_pole_the_validator_can_find(court_id, tmp_path):
    # validate_scene looks obstacles up by the names "Backboard" and "Pole".
    c = court(court_id)
    stage = build(c, tmp_path)
    names = [p.GetName() for p in stage.Traverse()]
    assert names.count("Backboard") == c["hoops"]
    assert names.count("Pole") == c["hoops"]


def test_scene_uses_z_up_and_meters(tmp_path):
    stage = build(court("gso_indoor_01"), tmp_path)
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0


def test_front_side_stays_open_for_viewing(tmp_path):
    stage = build(court("gso_indoor_01"), tmp_path)
    assert not stage.GetPrimAtPath("/World/Surroundings/WallS").IsValid()
    assert stage.GetPrimAtPath("/World/Surroundings/WallN").IsValid()


def test_ball_asset_is_a_separate_file_with_the_right_radius(tmp_path):
    path = tmp_path / ct.BALL_ASSET
    ct.author_ball_asset(path)
    stage = Usd.Stage.Open(str(path))       # keep a reference, or the prim becomes invalid
    body = stage.GetPrimAtPath("/Ball/Body")
    assert UsdGeom.Sphere(body).GetRadiusAttr().Get() == ct.BALL_RADIUS


# ---------------------------------------------------------------------------
# Whole pipeline
# ---------------------------------------------------------------------------
def test_dry_run_validates_and_writes_nothing(tmp_path):
    out = tmp_path / "out"
    result = ct.process_court(court("gso_indoor_01"), out, dryrun=True)
    assert result["status"] == "validated" and result["errors"] == []
    assert not out.exists()


def test_export_writes_court_and_ball_with_97_time_samples(tmp_path):
    out = tmp_path / "out"
    result = ct.process_court(court("gso_indoor_01"), out, dryrun=False)
    assert result["status"] == "exported"
    assert (out / ct.BALL_ASSET).exists()
    stage = Usd.Stage.Open(str(out / result["usd_file"]))
    ball = stage.GetPrimAtPath("/World/Ball")
    assert len(UsdGeom.Xformable(ball).GetTimeSamples()) == 97
    assert stage.GetPrimAtPath("/World/Ball/Body").IsValid()     # the reference resolved
    assert stage.GetPrimAtPath("/World").GetCustomDataByKey("igotnext")["court_id"] == "gso_indoor_01"


def test_bad_listing_is_rejected_before_anything_is_built(tmp_path):
    out = tmp_path / "out"
    result = ct.process_court(BAD[0], out, dryrun=False)
    assert result["status"] == "failed"
    assert "rim_height_m" in result["errors"][0]
    assert not out.exists()


def test_a_missed_shot_blocks_export(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "RIM_RADIUS", 0.1)          # rim smaller than the ball
    out = tmp_path / "out"
    result = ct.process_court(court("gso_outdoor_02"), out, dryrun=False)
    assert result["status"] == "failed"
    assert any("misses the rim" in e for e in result["errors"])
    assert not out.exists()


def test_a_ball_that_hits_the_backboard_blocks_export(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "MIN_CLEARANCE", 10.0)      # demand impossible clearance
    result = ct.process_court(court("gso_outdoor_02"), tmp_path / "out", dryrun=True)
    assert result["status"] == "failed"
    assert any("clearance" in e for e in result["errors"])


@pytest.mark.parametrize("c", GOOD, ids=lambda c: c["court_id"])
def test_every_good_court_in_the_spec_passes_the_full_pipeline(c, tmp_path):
    assert ct.process_court(c, tmp_path / "out", dryrun=True)["status"] == "validated"


# ---------------------------------------------------------------------------
# Command line entry point
# ---------------------------------------------------------------------------
def test_main_exits_1_when_any_court_fails_and_writes_no_manifest_on_dry_run(tmp_path):
    code = ct.main(["--spec", str(SPEC_PATH), "--dryrun", "--out", str(tmp_path)])
    assert code == 1
    assert not (tmp_path / "manifest.json").exists()


def test_main_exits_0_for_a_single_good_court(tmp_path):
    assert ct.main(["--spec", str(SPEC_PATH), "--court", "gso_indoor_01", "--dryrun", "--out", str(tmp_path)]) == 0


def test_main_exits_2_for_an_unknown_court(tmp_path):
    assert ct.main(["--spec", str(SPEC_PATH), "--court", "nope", "--out", str(tmp_path)]) == 2


def test_main_writes_a_manifest_listing_every_court_status(tmp_path):
    ct.main(["--spec", str(SPEC_PATH), "--out", str(tmp_path)])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    statuses = {c["court_id"]: c["status"] for c in manifest["courts"]}
    assert manifest["fps"] == 24.0
    assert statuses["gso_indoor_01"] == "exported"
    assert statuses["bad_example_99"] == "failed"
    assert len(statuses) == len(SPEC)
