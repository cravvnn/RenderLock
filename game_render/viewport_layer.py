"""Blender objects drawn live over the game image in the rendered viewport.

The engine draws the game frame and writes the game's own depth into the viewport
depth buffer (Blender 5.3 `bl_write_viewport_depth`), then draws the objects of the
"Blender objects" collection, lit by that collection's own lights, with a depth test. Game walls
and heroes therefore hide the objects exactly as in the final render, and Blender's
own overlays (selection outlines, gizmos, grid) are hidden by them too.

Final renders still come from EEVEE or Cycles through the compositor merge; this is
the live preview for placing, animating and lighting objects. Lights use Blender's own
units (measured against EEVEE), without shadows and without the game map's light.
"""
from __future__ import annotations

import math

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

UNIT = 0.0254  # metres per Source unit
MAX_LIGHTS = 8

_SHADERS = {}
_BATCHES = {}      # object name -> (key, batch)
_DIRTY = set()     # names of objects whose geometry changed


def _game_shader():
    shader = _SHADERS.get("game")
    if shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        interface = gpu.types.GPUStageInterfaceInfo("renderlock_game_iface")
        interface.smooth('VEC2', "uv")
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_out(interface)
        info.push_constant('VEC4', "rect")        # x, y, w, h in normalized device coordinates
        info.push_constant('VEC4', "projection")  # window_matrix [2][2], [2][3], depth scale, has depth
        info.push_constant('FLOAT', "has_image")
        info.sampler(0, 'FLOAT_2D', "image")
        info.sampler(1, 'FLOAT_2D', "depth")
        info.fragment_out(0, 'VEC4', "color")
        info.vertex_source(
            "void main() {"
            "  uv = pos;"
            "  gl_Position = vec4(rect.xy + pos * rect.zw, 0.0, 1.0);"
            "}")
        info.fragment_source(
            "void main() {"
            "  color = has_image > 0.5 ? texture(image, uv) : vec4(0.0);"
            "  float written = 1.0;"
            "  if (projection.w > 0.5) {"
            "    float distance = texture(depth, uv).r * projection.z;"
            "    if (distance > 0.0 && distance < 1.0e7) {"
            "      float z = -distance;"
            "      float ndc = (projection.x * z + projection.y) / -z;"
            "      written = clamp(ndc * 0.5 + 0.5, 0.0, 1.0);"
            "    }"
            "  }"
            "  gl_FragDepth = written;"
            "}")
        shader = _SHADERS["game"] = gpu.shader.create_from_info(info)
        del info, interface
    return shader


def _object_shader():
    shader = _SHADERS.get("object")
    if shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        interface = gpu.types.GPUStageInterfaceInfo("renderlock_object_iface")
        interface.smooth('VEC3', "normal_view")
        interface.smooth('VEC3', "position_view")
        info.vertex_in(0, 'VEC3', "pos")
        info.vertex_in(1, 'VEC3', "nor")
        info.vertex_out(interface)
        info.push_constant('MAT4', "model_view")
        info.push_constant('MAT4', "projection_matrix")
        info.push_constant('VEC4', "base_color")
        info.push_constant('VEC4', "emission")
        info.push_constant('FLOAT', "light_count")
        info.push_constant('VEC4', "light_position", MAX_LIGHTS)   # view space; w: 0 sun, 1 point, 2 spot, 3 area
        info.push_constant('VEC4', "light_direction", MAX_LIGHTS)  # view space, the way it points; w: spot blend
        info.push_constant('VEC4', "light_color", MAX_LIGHTS)      # colour times strength; w: cos(half spot angle)
        info.fragment_out(0, 'VEC4', "color")
        info.vertex_source(
            "void main() {"
            "  vec4 view_position = model_view * vec4(pos, 1.0);"
            "  normal_view = normalize(mat3(model_view) * nor);"
            "  position_view = view_position.xyz;"
            "  gl_Position = projection_matrix * view_position;"
            "}")
        info.fragment_source(
            "void main() {"
            "  vec3 n = normalize(normal_view);"
            "  if (!gl_FrontFacing) n = -n;"
            "  vec3 key = normalize(vec3(-0.35, 0.55, 0.75));"
            "  float diffuse = max(dot(n, key), 0.0);"
            "  float rim = pow(1.0 - max(n.z, 0.0), 3.0) * 0.25;"
            "  vec3 lit;"
            "  if (light_count < 0.5) {"
            "    lit = base_color.rgb * (0.28 + 0.72 * diffuse) + rim;"
            "  } else {"
            # A dim fill keeps unlit sides readable; the lights add on top in Blender's units.
            "    vec3 received = vec3(0.10 + 0.12 * diffuse);"
            "    for (int i = 0; i < int(light_count + 0.5); i++) {"
            "      float kind = light_position[i].w;"
            "      vec3 to_light = kind < 0.5 ? -light_direction[i].xyz : light_position[i].xyz - position_view;"
            "      float squared = kind < 0.5 ? 1.0 : max(dot(to_light, to_light), 1.0e-4);"
            "      vec3 l = normalize(to_light);"
            "      float amount = max(dot(n, l), 0.0) / squared;"
            "      float along = dot(-l, light_direction[i].xyz);"
            "      if (kind > 1.5 && kind < 2.5) {"
            "        float edge = light_color[i].w;"
            "        float blend = max(light_direction[i].w * (1.0 - edge), 1.0e-4);"
            "        amount *= smoothstep(0.0, 1.0, (along - edge) / blend);"
            "      }"
            "      if (kind > 2.5) amount *= max(along, 0.0);"
            "      received += light_color[i].rgb * amount;"
            "    }"
            "    lit = base_color.rgb * received / 3.14159265 + emission.rgb;"
            "    lit = lit / (1.0 + 0.35 * max(lit.r, max(lit.g, lit.b)));"
            "  }"
            "  color = vec4(pow(max(lit, vec3(0.0)), vec3(1.0 / 2.2)), base_color.a);"
            "}")
        shader = _SHADERS["object"] = gpu.shader.create_from_info(info)
        del info, interface
    return shader


def free():
    _SHADERS.clear()
    _BATCHES.clear()
    _DIRTY.clear()


def mark_dirty(names):
    _DIRTY.update(names)


def depth_texture(depth):
    """Game depth (Source units, top-down rows) as a float texture the shader samples bottom-up."""
    rows = np.ascontiguousarray(depth[::-1], dtype=np.float32)
    rows[~np.isfinite(rows)] = 0.0  # sky: nothing written, stays far
    height, width = rows.shape
    buffer = gpu.types.Buffer('FLOAT', width * height, rows.ravel())
    return gpu.types.GPUTexture((width, height), format='R32F', data=buffer)


def draw_game(color_texture, depth_tex, window_matrix, x, y, w, h, region_w, region_h):
    """The game image (if any) and its depth into the bound framebuffer, inside a pixel rectangle."""
    shader = _game_shader()
    quad = _SHADERS.get("quad")
    if quad is None:
        quad = _SHADERS["quad"] = batch_for_shader(shader, 'TRI_FAN', {"pos": ((0, 0), (1, 0), (1, 1), (0, 1))})
    rect = (2.0 * x / region_w - 1.0, 2.0 * y / region_h - 1.0, 2.0 * w / region_w, 2.0 * h / region_h)
    gpu.state.depth_test_set('ALWAYS')
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE')
    shader.bind()
    shader.uniform_float("rect", rect)
    shader.uniform_float("projection", (window_matrix[2][2], window_matrix[2][3], UNIT,
                                        1.0 if depth_tex is not None else 0.0))
    shader.uniform_float("has_image", 1.0 if color_texture is not None else 0.0)
    shader.uniform_sampler("image", color_texture if color_texture is not None else _blank())
    shader.uniform_sampler("depth", depth_tex if depth_tex is not None else _blank_depth())
    quad.draw(shader)


def _blank():
    texture = _SHADERS.get("blank")
    if texture is None:
        texture = _SHADERS["blank"] = gpu.types.GPUTexture((1, 1), format='RGBA8')
    return texture


def _blank_depth():
    texture = _SHADERS.get("blank_depth")
    if texture is None:
        texture = _SHADERS["blank_depth"] = gpu.types.GPUTexture(
            (1, 1), format='R32F', data=gpu.types.Buffer('FLOAT', 1, [0.0]))
    return texture


def _mesh_arrays(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        triangles = len(mesh.loop_triangles)
        if not triangles:
            return None
        loops = np.empty(triangles * 3, np.int32)
        mesh.loop_triangles.foreach_get("loops", loops)
        vertex_of_loop = np.empty(len(mesh.loops), np.int32)
        mesh.loops.foreach_get("vertex_index", vertex_of_loop)
        positions = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", positions)
        normals = np.empty(len(mesh.loops) * 3, np.float32)
        mesh.corner_normals.foreach_get("vector", normals)
        positions = positions.reshape(-1, 3)[vertex_of_loop[loops]]
        normals = normals.reshape(-1, 3)[loops]
        return np.ascontiguousarray(positions), np.ascontiguousarray(normals)
    finally:
        evaluated.to_mesh_clear()


def _batch(obj, depsgraph):
    key = (obj.data.name if obj.data else "", obj.type)
    cached = _BATCHES.get(obj.name)
    if cached is not None and cached[0] == key and obj.name not in _DIRTY:
        return cached[1]
    _DIRTY.discard(obj.name)
    arrays = _mesh_arrays(obj, depsgraph) if obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'} else None
    batch = batch_for_shader(_object_shader(), 'TRIS', {"pos": arrays[0], "nor": arrays[1]}) if arrays else None
    _BATCHES[obj.name] = (key, batch)
    return batch


def _color(obj):
    """Base colour and emission: the Principled BSDF's own values when the material has one."""
    material = obj.active_material
    if material is None:
        return tuple(obj.color), (0.0, 0.0, 0.0, 0.0)
    tree = material.node_tree
    shader = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) if tree else None
    if shader is None:
        return tuple(material.diffuse_color), (0.0, 0.0, 0.0, 0.0)
    base = tuple(shader.inputs["Base Color"].default_value)
    glow = shader.inputs.get("Emission Color")
    strength = shader.inputs.get("Emission Strength")
    if glow is None or strength is None or strength.default_value <= 0.0:
        return base, (0.0, 0.0, 0.0, 0.0)
    return base, tuple(c * strength.default_value for c in glow.default_value[:3]) + (0.0,)


def _light_uniforms(lights, depsgraph, view_matrix):
    """Blender lights as shader arrays. Strengths as EEVEE shows them on a diffuse surface:
    sun = W/m2, point and spot = W / 4pi per steradian, area = W / pi along its normal."""
    rows = []
    for obj in lights:
        evaluated = obj.evaluated_get(depsgraph)
        light = evaluated.data
        matrix = view_matrix @ evaluated.matrix_world
        position = matrix.translation
        direction = (matrix.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        kind = {'SUN': 0.0, 'POINT': 1.0, 'SPOT': 2.0, 'AREA': 3.0}.get(light.type, 1.0)
        scale = {0.0: 1.0, 3.0: 1.0 / math.pi}.get(kind, 1.0 / (4.0 * math.pi))
        energy = light.energy * scale * (2.0 ** getattr(light, "exposure", 0.0))
        edge = math.cos(light.spot_size / 2.0) if light.type == 'SPOT' else 0.0
        blend = light.spot_blend if light.type == 'SPOT' else 0.0
        weight = energy if kind < 0.5 else energy / max(position.length_squared, 1.0)
        rows.append((weight, (*position, kind), (*direction, blend), (*(c * energy for c in light.color), edge)))
    rows.sort(key=lambda row: -row[0])
    rows = rows[:MAX_LIGHTS]
    arrays = [np.zeros((MAX_LIGHTS, 4), np.float32) for _ in range(3)]
    for index, row in enumerate(rows):
        for array, values in zip(arrays, row[1:]):
            array[index] = values
    return len(rows), arrays


def draw_objects(objects, depsgraph, view_matrix, window_matrix, lights=()):
    """Blender objects, lit by the given Blender lights, depth-tested against what is in the buffer."""
    shader = _object_shader()
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(True)
    gpu.state.face_culling_set('NONE')
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("projection_matrix", window_matrix)
    count, arrays = _light_uniforms(lights, depsgraph, view_matrix)
    shader.uniform_float("light_count", float(count))
    for name, array in zip(("light_position", "light_direction", "light_color"), arrays):
        shader.uniform_vector_float(shader.uniform_from_name(name),
                                    gpu.types.Buffer('FLOAT', array.size, array.ravel()), 4, MAX_LIGHTS)
    drawn = 0
    for obj in objects:
        batch = _batch(obj, depsgraph)
        if batch is None:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        shader.uniform_float("model_view", view_matrix @ evaluated.matrix_world)
        base, glow = _color(obj)
        shader.uniform_float("base_color", base)
        shader.uniform_float("emission", glow)
        batch.draw(shader)
        drawn += 1
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')
    return drawn


def perspective(hfov_radians, aspect, near=0.05, far=5000.0):
    """OpenGL-style projection matching a horizontal field of view (as Blender's window_matrix)."""
    sx = 1.0 / np.tan(hfov_radians / 2)
    sy = sx * aspect
    return Matrix(((sx, 0, 0, 0), (0, sy, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)), (0, 0, -1, 0)))
