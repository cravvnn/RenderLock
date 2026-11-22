"""Blender objects drawn live over the game image in the rendered viewport.

The engine draws the game frame and writes the game's own depth into the viewport
depth buffer (Blender 5.3 `bl_write_viewport_depth`), then draws the objects of the
"Blender objects" collection with a plain solid shading and a depth test. Game walls
and heroes therefore hide the objects exactly as in the final render, and Blender's
own overlays (selection outlines, gizmos, grid) are hidden by them too.

Final renders still come from EEVEE or Cycles through the compositor merge; this is
the live preview for placing and animating objects.
"""
from __future__ import annotations

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

UNIT = 0.0254  # metres per Source unit

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
        info.vertex_in(0, 'VEC3', "pos")
        info.vertex_in(1, 'VEC3', "nor")
        info.vertex_out(interface)
        info.push_constant('MAT4', "model_view")
        info.push_constant('MAT4', "projection_matrix")
        info.push_constant('VEC4', "base_color")
        info.fragment_out(0, 'VEC4', "color")
        info.vertex_source(
            "void main() {"
            "  vec4 view_position = model_view * vec4(pos, 1.0);"
            "  normal_view = normalize(mat3(model_view) * nor);"
            "  gl_Position = projection_matrix * view_position;"
            "}")
        info.fragment_source(
            "void main() {"
            "  vec3 n = normalize(normal_view);"
            "  if (!gl_FrontFacing) n = -n;"
            "  vec3 key = normalize(vec3(-0.35, 0.55, 0.75));"
            "  float diffuse = max(dot(n, key), 0.0);"
            "  float rim = pow(1.0 - max(n.z, 0.0), 3.0) * 0.25;"
            "  vec3 lit = base_color.rgb * (0.28 + 0.72 * diffuse) + rim;"
            "  color = vec4(pow(lit, vec3(1.0 / 2.2)), base_color.a);"
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
    material = obj.active_material
    if material is not None:
        return tuple(material.diffuse_color)
    return tuple(obj.color)


def draw_objects(objects, depsgraph, view_matrix, window_matrix):
    """Blender objects with a solid shading, depth-tested against what is already in the buffer."""
    shader = _object_shader()
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(True)
    gpu.state.face_culling_set('NONE')
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("projection_matrix", window_matrix)
    drawn = 0
    for obj in objects:
        batch = _batch(obj, depsgraph)
        if batch is None:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        shader.uniform_float("model_view", view_matrix @ evaluated.matrix_world)
        shader.uniform_float("base_color", _color(obj))
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
