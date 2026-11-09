"""Open exported courts in Blender with colors, labels, camera framing and playback.

Usage (run court_twin.py first so output/ exists):
    blender --python open_in_blender.py                                  # every court in one scene
    blender --python open_in_blender.py -- output/gso_outdoor_02.usda    # one court
    blender --python open_in_blender.py -- a.usda b.usda                 # chosen courts

Why this script exists: Blender skips USD Preview Surface materials by default and
ignores material bindings on USD cube, sphere and cylinder shapes. So this script
imports with the right options, then assigns each material by object name.
"""
import glob
import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if args:
    FILES = [os.path.abspath(a) for a in args]
else:
    FILES = sorted(p for p in glob.glob(os.path.join(HERE, "output", "*.usda"))
                   if not p.endswith("basketball_asset.usda"))
if not FILES:
    raise SystemExit("No courts found. Run court_twin.py first so output/ exists.")
GALLERY = len(FILES) > 1
COLUMNS, SPACING_X, SPACING_Y = 3, 48.0, 40.0   # meters between court centers

# Clear the default cube, light and camera.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()


def base(name):
    """Blender adds .001 style suffixes when names repeat; drop them."""
    return name.split(".")[0]


def material_name_for(obj):
    n = base(obj.name)
    p = base(obj.parent.name) if obj.parent else ""
    if n == "Ground":
        return "Ground"
    if n.startswith("Wall"):
        return "Wall"
    if n.startswith("Fence"):
        return "Fence"
    if n.startswith("Backboard"):
        return "Board"
    if n.startswith("Pole"):
        return "Steel"
    if n == "Body":
        return "Leather"
    if n.startswith("Key"):
        return "Paint"
    if p == "Rim":
        return "Rim"
    if p == "Net":
        return "Net"
    if n in ("SideA", "SideB", "EndA", "EndB", "Center") or p.startswith(("ThreePoint", "FreeThrow", "CenterCircle")):
        return "Line"
    if n == "PlayingSurface":
        return "Floor"
    if n == "Floor":
        return "Apron"
    return None


def solid_material(name, color, emission=0.0):
    """A plain material built from nodes, so it renders the same in every mode."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def LABEL_MATERIAL():
    return bpy.data.materials.get("Label") or solid_material("Label", (1.0, 1.0, 1.0), emission=1.5)


def import_court(path, slot):
    """Import one court, give it materials, and move it to its grid slot."""
    objs_before, mats_before = set(bpy.data.objects), set(bpy.data.materials)
    bpy.ops.wm.usd_import(
        filepath=path,
        import_usd_preview=True,
        import_all_materials=True,
        mtl_purpose="MTL_ALL_PURPOSE",
        set_material_blend=True,
    )
    new_objs = [o for o in bpy.data.objects if o not in objs_before]
    mats = {base(m.name): m for m in bpy.data.materials if m not in mats_before}

    # Blender ignores material bindings on USD shapes, so assign by object name.
    for obj in new_objs:
        mat = mats.get(material_name_for(obj) or "")
        if mat and obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(mat)

    if GALLERY:
        col, row = slot % COLUMNS, slot // COLUMNS
        cx, cy = col * SPACING_X, row * SPACING_Y
        for obj in new_objs:
            if obj.parent is None:
                obj.location = (cx, cy, 0.0)
            if base(obj.name) == "Ground" or base(obj.name).startswith(("Wall", "Fence")):
                bpy.data.objects.remove(obj)      # walls would hide the courts behind them
        label = os.path.splitext(os.path.basename(path))[0]
        bpy.ops.object.text_add(location=(cx, cy - 13.5, 0.05), rotation=(math.radians(90), 0, 0))
        text = bpy.context.object
        text.name = f"Label_{label}"
        text.data.body = label
        text.data.size = 2.4
        text.data.align_x = "CENTER"
        text.data.materials.append(LABEL_MATERIAL())
    return new_objs


for i, f in enumerate(FILES):
    import_court(f, i)

scene = bpy.context.scene
scene.frame_start = 0
scene.frame_end = 96          # 97 ticks at 24 per second
scene.render.fps = 24
scene.frame_set(0)

if GALLERY:
    mat = solid_material("GalleryGround", (0.02, 0.025, 0.05))
    bpy.ops.mesh.primitive_plane_add(size=600, location=((COLUMNS - 1) * SPACING_X / 2, SPACING_Y / 2, -0.12))
    ground = bpy.context.object
    ground.name = "GalleryGround"
    ground.data.materials.append(mat)

# Background: sky blue for one outdoor court, dusk for a gallery, dark arena for one indoor court.
outdoor = len(FILES) == 1 and "outdoor" in os.path.basename(FILES[0])
if GALLERY:
    sky = (0.22, 0.28, 0.45, 1.0)
else:
    sky = (0.45, 0.70, 1.0, 1.0) if outdoor else (0.06, 0.06, 0.12, 1.0)
world = bpy.data.worlds.new("CourtWorld")
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = sky
bg.inputs[1].default_value = 1.0
scene.world = world
scene.view_settings.view_transform = "Standard"   # the default (AgX) washes the colors out
sun_data = bpy.data.lights.new("CourtSun", "SUN")
sun_data.energy = 4.0
sun_data.angle = math.radians(12)                 # soft shadows
sun = bpy.data.objects.new("CourtSun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (0.9, 0.2, 0.6)


def finish():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                shading = area.spaces[0].shading
                shading.type = "MATERIAL"
                try:
                    shading.use_scene_world = True
                    shading.use_scene_lights = True
                    area.spaces[0].overlay.show_extras = False   # hides the black empty markers
                    area.spaces[0].overlay.show_floor = False    # hides the grid
                except Exception as exc:                         # older or newer Blender builds
                    print("viewport setup skipped:", exc)
                region = next(r for r in area.regions if r.type == "WINDOW")
                with bpy.context.temp_override(window=win, area=area, region=region):
                    bpy.ops.view3d.view_all()
    bpy.ops.screen.animation_play()
    return None


bpy.app.timers.register(finish, first_interval=1.0)
