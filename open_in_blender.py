"""Open an exported court in Blender with colors, camera framing and playback.

Usage (run court_twin.py first so output/ exists):
    blender --python open_in_blender.py
    blender --python open_in_blender.py -- output/gso_outdoor_02.usda

Why this script exists: Blender skips USD Preview Surface materials by default and
ignores material bindings on USD cube, sphere and cylinder shapes. So this script
imports with the right options, then assigns each material by object name.
"""
import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
USD = os.path.abspath(args[0]) if args else os.path.join(HERE, "output", "gso_indoor_01.usda")

# Clear the default cube, light and camera.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()

bpy.ops.wm.usd_import(
    filepath=USD,
    import_usd_preview=True,
    import_all_materials=True,
    mtl_purpose="MTL_ALL_PURPOSE",
    set_material_blend=True,
)


def base(name):
    """Blender adds .001 style suffixes when names repeat; drop them."""
    return name.split(".")[0]


def material_for(obj):
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


for obj in bpy.data.objects:
    mat = bpy.data.materials.get(material_for(obj) or "")
    if mat and obj.type == "MESH":
        obj.data.materials.clear()
        obj.data.materials.append(mat)

scene = bpy.context.scene
scene.frame_start = 0
scene.frame_end = 96          # 97 ticks at 24 per second
scene.render.fps = 24
scene.frame_set(0)

# Background: a sky blue world for outdoor courts, a dark arena for indoor ones,
# plus a sun so the glossy floor and metal parts have something to reflect.
outdoor = "outdoor" in os.path.basename(USD)
world = bpy.data.worlds.new("CourtWorld")
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.45, 0.70, 1.0, 1.0) if outdoor else (0.06, 0.06, 0.12, 1.0)
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
