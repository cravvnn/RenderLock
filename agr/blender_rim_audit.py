"""Same-render linear AOV checks for the research rim preview."""
import json
import bpy
import numpy as np

PASSES = [('AGR rim diagnostics', 'COLOR'), ('AGR rim coverage', 'VALUE'),
          ('AGR rim only', 'COLOR'), ('AGR base lighting', 'COLOR'),
          ('AGR final lighting', 'COLOR')]


def register():
    layer = bpy.context.scene.view_layers[0]
    if any(a.name in dict(PASSES) for a in layer.aovs):
        raise ValueError('Rim audit AOV name collision')
    for name, kind in PASSES:
        aov = layer.aovs.add(); aov.name = name; aov.type = kind


def attach(nodes, links, project, depths, rim, npr, total, *, base_socket=None):
    diagnostic = nodes.new('ShaderNodeCombineXYZ')
    links.new(project.outputs['Valid'], diagnostic.inputs['X'])
    background = nodes.new('ShaderNodeMath'); background.operation = 'GREATER_THAN'
    background.inputs[1].default_value = 1e8
    links.new(depths.outputs['Sampled Depth'], background.inputs[0])
    links.new(background.outputs[0], diagnostic.inputs['Y'])
    links.new(rim.outputs['Factor'], diagnostic.inputs['Z'])
    for name, socket in [('AGR rim diagnostics', diagnostic.outputs[0]),
                         ('AGR rim only', rim.outputs['Rim']),
                         ('AGR base lighting', npr.outputs['Total'] if base_socket is None else base_socket),
                         ('AGR final lighting', total.outputs[0])]:
        aov = nodes.new('ShaderNodeOutputAOV'); aov.aov_name = name
        links.new(socket, aov.inputs['Color'])
    aov = nodes.new('ShaderNodeOutputAOV'); aov.aov_name = 'AGR rim coverage'
    aov.inputs['Value'].default_value = 1


def compositor(out):
    scene = bpy.context.scene
    scene.view_layers[0].update_render_passes()
    group = bpy.data.node_groups.new('Rim audit', 'CompositorNodeTree')
    scene.compositing_node_group = group
    render = group.nodes.new('CompositorNodeRLayers')
    dest = group.nodes.new('CompositorNodeOutputFile')
    dest.directory = str(out); dest.file_name = 'rim-audit'
    dest.format.media_type = 'IMAGE'; dest.format.file_format = 'OPEN_EXR'
    dest.format.color_depth = '32'
    for name, kind in PASSES:
        dest.file_output_items.new('RGBA' if kind == 'COLOR' else 'FLOAT', name)
        group.links.new(render.outputs[name], dest.inputs[name])


def verify(out, frame, width, height):
    results = {}
    for name, _ in PASSES:
        paths = list(out.glob('*' + name + '*.exr'))
        if len(paths) != 1:
            raise ValueError('Expected one AOV file: ' + name)
        image = bpy.data.images.load(str(paths[0]))
        results[name] = np.array(image.pixels[:]).reshape(height, width, 4)
    finite = all(np.isfinite(a).all() for a in results.values())
    coverage = results['AGR rim coverage'][:, :, 0]
    diag = results['AGR rim diagnostics'][:, :, :3]
    rim = results['AGR rim only'][:, :, :3]
    invalid = np.maximum(coverage - diag[:, :, 0], 0)
    error = float(np.max(abs(results['AGR final lighting'][:, :, :3]
                            - results['AGR base lighting'][:, :, :3] - rim)))
    background = int(np.sum(diag[:, :, 1] > 1e-6))
    covered = int(np.sum(coverage > 1e-6))
    passed = finite and covered > 0 and invalid.max() < 1e-5 and background == 0 and error < 2e-6
    report = dict(status='PASS' if passed else 'FAIL', frame=frame,
        all_finite=finite, hero_covered_pixels=covered,
        invalid_projection_weight=float(invalid.sum()),
        max_invalid_projection_weight=float(invalid.max()),
        background_sample_pixels=background,
        positive_rim_pixels=int(np.sum(np.max(rim, axis=2) > 1e-6)),
        max_rim_rgb=float(rim.max()), max_linear_addition_error=error,
        scope='Antialiased shader AOVs; background >1e8 Source units is a renderer sentinel classifier, not engine semantics')
    (out/'rim-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(report)
    if not passed:
        raise ValueError('Rim AOV audit failed; see rim-audit.json')
    return report
