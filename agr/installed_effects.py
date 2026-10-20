"""Prepare a conservative, documented Source 2 particle subset from the installation.

This is a renderer adapter, not the Source 2 particle simulator. Unknown layers
are listed in the report and never replaced with generic flashes. All resource
definitions and images are read from the user's installation, not this package.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
import shutil

from .asset_converter import ConversionError, compiled
from .game_assets import GameAssetError
from .coverage_diagnostics import effect_preparation_status, preparation_summary
from .vendor.resource_hash import resource_id


class UnsupportedEffect(ValueError):
    pass


def scalar(value, rng=None, default=0.0, age=None):
    if value is None:
        return float(default)
    if not isinstance(value, dict):
        result = float(value)
    else:
        kind = value.get('m_nType', 'PF_TYPE_LITERAL')
        mapping = value.get('m_nMapType', 'PF_MAP_TYPE_DIRECT')
        if kind == 'PF_TYPE_LITERAL':
            result = float(value.get('m_flLiteralValue', default))
        elif kind in ('PF_TYPE_RANDOM_UNIFORM', 'PF_TYPE_RANDOM_BIASED') and rng is not None:
            # The engine seed is absent from the demo. Uniform sampling of biased
            # providers is a declared approximation, retained from the scene test.
            result = rng.uniform(value.get('m_flRandomMin', 0), value.get('m_flRandomMax', 1))
            if value.get('m_bHasRandomSignFlip') and rng.random() < .5:
                result = -result
        elif kind == 'PF_TYPE_COLLECTION_AGE' and age is not None:
            result = float(age)
        else:
            raise UnsupportedEffect('Scalar provider ' + kind)
        if mapping == 'PF_MAP_TYPE_CURVE':
            result = hermite(value.get('m_Curve', {}).get('m_spline', []), result)
        elif mapping != 'PF_MAP_TYPE_DIRECT':
            raise UnsupportedEffect('Scalar mapping ' + mapping)
    if not math.isfinite(result):
        raise UnsupportedEffect('Non-finite particle scalar')
    return result


def vector(value, default=(0, 0, 0)):
    if isinstance(value, dict):
        if value.get('m_nType', 'PVEC_TYPE_LITERAL') != 'PVEC_TYPE_LITERAL':
            raise UnsupportedEffect('Vector provider ' + value['m_nType'])
        value = value.get('m_vLiteralValue', default)
    result = list(map(float, default if value is None else value))
    if len(result) != 3 or not all(math.isfinite(v) for v in result):
        raise UnsupportedEffect('Invalid particle vector')
    return result


def hermite(points, value):
    if not points or any(b['x'] <= a['x'] for a, b in zip(points, points[1:])):
        raise UnsupportedEffect('Missing or unordered scalar curve')
    if value <= points[0]['x']:
        return float(points[0]['y'])
    if value >= points[-1]['x']:
        return float(points[-1]['y'])
    for a, b in zip(points, points[1:]):
        if value <= b['x']:
            span = b['x'] - a['x']
            t = (value - a['x']) / span
            return ((2*t**3-3*t*t+1)*a['y'] + (t**3-2*t*t+t)*span*a.get('m_flSlopeOutgoing', 0)
                    + (-2*t**3+3*t*t)*b['y'] + (t**3-t*t)*span*b.get('m_flSlopeIncoming', 0))


def fade(definition, normalized):
    value = 1.0
    for op in definition.get('m_Operators', []):
        if op['_class'] != 'C_OP_FadeAndKill':
            continue
        a, b = op.get('m_flStartFadeInTime', 0), op.get('m_flEndFadeInTime', .5)
        start = op.get('m_flStartAlpha', 1)
        if normalized < a:
            value *= start
        elif normalized < b and b > a:
            value *= start + (1-start)*(normalized-a)/(b-a)
        a, b = op.get('m_flStartFadeOutTime', .5), op.get('m_flEndFadeOutTime', 1)
        end = op.get('m_flEndAlpha', 0)
        if normalized >= b:
            value *= end
        elif normalized >= a and b > a:
            value *= 1 + (end-1)*(normalized-a)/(b-a)
    return max(0, value)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def classify_layer(definition):
    """Reject unsupported physics/emitters before creating Blender objects."""
    if definition.get('_class') != 'CParticleSystemDefinition':
        raise UnsupportedEffect('Not a particle-system definition')
    emitters = definition.get('m_Emitters', [])
    if not emitters:
        raise UnsupportedEffect('No emitter in this layer')
    if len(emitters) != 1 or emitters[0].get('_class') != 'C_OP_InstantaneousEmitter':
        raise UnsupportedEffect('Only single instantaneous emitters are implemented')
    if scalar(emitters[0].get('m_flStartTime')) != 0:
        raise UnsupportedEffect('Delayed emitter')
    if any(op.get('_class') == 'C_INIT_RingWave' for op in definition.get('m_Initializers', [])):
        return classify_ribbon(definition)
    for pre in definition.get('m_PreEmissionOperators', []):
        # Seven uses a scalar CP10 expression for child effects. It does not
        # affect a layer whose numeric providers never read CP10.
        if pre.get('_class') != 'C_OP_SetControlPointFieldToScalarExpression':
            raise UnsupportedEffect('Pre-emission operator ' + pre.get('_class', '?'))
        cp = pre.get('m_nOutputCP', 0)
        for group in ('m_Initializers', 'm_Operators', 'm_Renderers'):
            if any(v.get('m_nControlPoint') == cp and v.get('m_nType', '').endswith('CONTROL_POINT')
                   for v in _walk(definition.get(group, []))):
                raise UnsupportedEffect('Layer consumes computed control point')
    allowed_initializers = {'C_INIT_InitFloat', 'C_INIT_CreateWithinSphereTransform',
                            'C_INIT_PositionOffset', 'C_INIT_RandomColor', 'C_INIT_RandomSequence'}
    for op in definition.get('m_Initializers', []):
        if op['_class'] not in allowed_initializers:
            raise UnsupportedEffect('Initializer ' + op['_class'])
        if op['_class'] == 'C_INIT_InitFloat' and op.get('m_nOutputField', 3) not in (1, 3, 4, 7, 10):
            raise UnsupportedEffect('Initializer output field ' + str(op.get('m_nOutputField')))
        if op['_class'] == 'C_INIT_CreateWithinSphereTransform':
            if scalar(op.get('m_fRadiusMax')) != 0 or scalar(op.get('m_fRadiusMin')) != 0:
                raise UnsupportedEffect('Nonzero random sphere radius')
            if scalar(op.get('m_fSpeedMin')) != 0 or scalar(op.get('m_fSpeedMax')) != 0:
                raise UnsupportedEffect('Radial sphere velocity')
    allowed_operators = {'C_OP_FadeAndKill', 'C_OP_PositionLock', 'C_OP_InterpolateRadius',
                         'C_OP_BasicMovement', 'C_OP_Decay', 'C_OP_RampScalarLinearSimple', 'C_OP_SetFloat'}
    operators = definition.get('m_Operators', [])
    if not any(op['_class'] == 'C_OP_PositionLock' for op in operators):
        raise UnsupportedEffect('Only attachment-locked layers are implemented')
    for op in operators:
        if op['_class'] not in allowed_operators:
            raise UnsupportedEffect('Operator ' + op['_class'])
        if op['_class'] == 'C_OP_PositionLock' and op.get('m_nControlPointNumber', 0) != 0:
            raise UnsupportedEffect('Position lock requires another control point')
        if op['_class'] == 'C_OP_BasicMovement' and any(vector(op.get('m_Gravity'))):
            raise UnsupportedEffect('Gravity movement')
        if op['_class'] == 'C_OP_SetFloat':
            if op.get('m_nOutputField', 3) != 3 or op.get('m_nSetMethod') != 'PARTICLE_SET_SCALE_INITIAL_VALUE':
                raise UnsupportedEffect('SetFloat combination')
            scalar(op.get('m_InputValue'), age=0)
        if op['_class'] in ('C_OP_RampScalarLinearSimple', 'C_OP_Decay') and op.get('m_nOpEndCapState') != 'PARTICLE_ENDCAP_ENDCAP_ON':
            raise UnsupportedEffect('Non-endcap decay/ramp')
    renderers = []
    omissions = []
    for renderer in definition.get('m_Renderers', []):
        if renderer.get('m_bOnlyRenderInEffectsBloomPass'):
            omissions.append('Source bloom-only renderer omitted')
            continue
        if renderer['_class'] not in ('C_OP_RenderSprites', 'C_OP_RenderTrails'):
            raise UnsupportedEffect('Renderer ' + renderer['_class'])
        if renderer.get('m_nOutputBlendMode', 'PARTICLE_OUTPUT_BLEND_MODE_ALPHA') != 'PARTICLE_OUTPUT_BLEND_MODE_ADD':
            raise UnsupportedEffect('Non-additive blend mode')
        textures = renderer.get('m_vecTexturesInput', [])
        if not textures or not textures[0].get('m_hTexture'):
            raise UnsupportedEffect('Missing source texture')
        for texture in textures:
            if texture.get('m_bReplaceTextureWithGradient'):
                omissions.append('Source color lookup gradient approximated by its first authored color')
                continue
            if texture.get('m_nTextureType', 'SPRITECARD_TEXTURE_RGBA') != 'SPRITECARD_TEXTURE_RGBA':
                raise UnsupportedEffect('Non-RGBA texture layer')
            if texture.get('m_nTextureBlendMode', 'SPRITECARD_TEXTURE_BLEND_MULTIPLY') != 'SPRITECARD_TEXTURE_BLEND_MULTIPLY':
                raise UnsupportedEffect('Texture-layer blend mode')
        renderers.append(renderer)
    if not renderers:
        raise UnsupportedEffect('No supported visual renderer')
    persistent = any(op['_class'] == 'C_OP_SetFloat' for op in operators)
    if persistent and not any(op['_class'] == 'C_OP_Decay' for op in operators):
        raise UnsupportedEffect('Persistent collection without supported endcap decay')
    return {'kind': 'attachment_charge' if persistent else 'attachment_burst',
            'renderers': renderers, 'omissions': omissions}


def classify_ribbon(definition):
    """Authored initial ring ribbon, with explicit omissions from its simulation."""
    initializers = definition.get('m_Initializers', [])
    if [op['_class'] for op in initializers] != ['C_INIT_RingWave', 'C_INIT_PositionOffset',
                                               'C_INIT_InitFloat', 'C_INIT_InitFloat', 'C_INIT_InitFloat']:
        raise UnsupportedEffect('Ring ribbon initializer layout')
    if not initializers[0].get('m_bEvenDistribution'):
        raise UnsupportedEffect('Uneven ring distribution')
    if initializers[2].get('m_nOutputField', 3) != 3 or initializers[4].get('m_nOutputField') != 7:
        raise UnsupportedEffect('Ring width/taper fields')
    allowed = {'C_OP_PositionLock', 'C_OP_OscillateScalar', 'C_OP_RemapVelocityToVector',
               'C_OP_MovementRotateParticleAroundAxis', 'C_OP_FadeInSimple',
               'C_OP_EndCapTimedDecay', 'C_OP_LerpEndCapScalar'}
    if any(op['_class'] not in allowed for op in definition.get('m_Operators', [])):
        raise UnsupportedEffect('Unsupported ring operator')
    for op in definition.get('m_Operators', []):
        if op['_class'] == 'C_OP_MovementRotateParticleAroundAxis' and scalar(op.get('m_flRotRate')) != 0:
            raise UnsupportedEffect('Nonzero ring rotation rate')
    renderers = definition.get('m_Renderers', [])
    if not renderers or any(r['_class'] != 'C_OP_RenderRopes' or
            r.get('m_nOutputBlendMode') != 'PARTICLE_OUTPUT_BLEND_MODE_ADD' or
            r.get('m_nOrientationType') != 'PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL'
            for r in renderers):
        raise UnsupportedEffect('Ring ribbon renderer orientation/blending')
    return {'kind': 'recorded_ring_ribbon', 'renderers': renderers,
            'omissions': ['Initial ring profile is reconstructed; radial speed, width/alternate-alpha oscillation and particle normals are approximated.']}


def ribbon_particle(definition, instance, tick_rate, clip_end):
    initializers = definition['m_Initializers']
    ring = initializers[0]
    cp = ring.get('m_TransformInput', {}).get('m_nControlPoint', 0)
    updates = [dict(e['data']['update_particle_transform'], tick=e['tick'])
               for e in instance.get('events', []) if 'update_particle_transform' in e.get('data', {})]
    transforms = [e for e in updates if e.get('control_point', 0) == cp and 'position' in e]
    if not transforms:
        raise UnsupportedEffect('Ring lacks recorded transform control point')
    radius_provider = ring.get('m_flInitialRadius', {})
    if radius_provider.get('m_nType') != 'PF_TYPE_CONTROL_POINT_COMPONENT' or radius_provider.get('m_nMapType') != 'PF_MAP_TYPE_MULT':
        raise UnsupportedEffect('Ring radius provider')
    radius_updates = [e for e in updates if e.get('control_point', 0) == radius_provider.get('m_nControlPoint', 0) and 'position' in e]
    if not radius_updates:
        raise UnsupportedEffect('Ring lacks recorded radius control point')
    values = {tuple(e['position'].get(k, 0) for k in 'xyz') for e in radius_updates}
    if len(values) != 1:
        raise UnsupportedEffect('Changing ring radius control point')
    radius = next(iter(values))[radius_provider.get('m_nVectorComponent', 0)]*radius_provider.get('m_flMultFactor', 1)
    count = round(scalar(definition['m_Emitters'][0].get('m_nParticlesToEmit')))
    orbit = scalar(ring.get('m_flParticlesPerOrbit'), default=1)
    if not 2 <= count <= 128 or orbit <= 0 or radius <= 0:
        raise UnsupportedEffect('Invalid ring geometry')
    offset = vector(initializers[1].get('m_OffsetMin'))
    if offset != vector(initializers[1].get('m_OffsetMax')):
        raise UnsupportedEffect('Random ribbon position offset')
    points = initializers[4].get('m_InputValue', {}).get('m_Curve', {}).get('m_spline', [])
    taper = [max(0., min(1., hermite(points, i/(count-1)))) for i in range(count)]
    fade_in = next((o.get('m_flFadeInTime', .5) for o in definition.get('m_Operators', []) if o['_class'] == 'C_OP_FadeInSimple'), 0)
    decay = next((o.get('m_flDecayTime', 0) for o in definition.get('m_Operators', []) if o['_class'] == 'C_OP_EndCapTimedDecay'), 0)
    stop = instance.get('destroy_tick')
    end = clip_end+1 if stop is None else stop+(0 if instance.get('destroy_immediately') else decay*tick_rate)
    return {'index': 0, 'born_tick': instance['created_tick'], 'end_tick': end,
            'radius': radius, 'life': 1., 'trail': 1., 'alpha': .12, 'sequence': 0,
            'color': [v/255 for v in definition.get('m_ConstantColor', [255]*4)[:3]],
            'count': count, 'orbit': orbit, 'yaw': scalar(ring.get('m_flYaw')), 'offset': offset,
            'width': scalar(initializers[2].get('m_InputValue')), 'taper': taper,
            'fade_in': fade_in, 'decay': decay, 'transforms': transforms}


def initialize_particle(definition, rng):
    p = {'radius': definition.get('m_flConstantRadius', 5), 'life': 1., 'trail': 1.,
         'alpha': 1., 'rotation': 0., 'sequence': definition.get('m_nConstantSequenceNumber', 0),
         'color': [v/255 for v in definition.get('m_ConstantColor', [255]*4)[:3]],
         'velocity': [0., 0., 0.], 'offset': [0., 0., 0.]}
    fields = {1: 'life', 3: 'radius', 4: 'rotation', 7: 'alpha', 10: 'trail'}
    for op in definition.get('m_Initializers', []):
        kind = op['_class']
        if kind == 'C_INIT_InitFloat':
            p[fields[op.get('m_nOutputField', 3)]] = scalar(op.get('m_InputValue'), rng)
        elif kind == 'C_INIT_CreateWithinSphereTransform':
            lo, hi = vector(op.get('m_LocalCoordinateSystemSpeedMin')), vector(op.get('m_LocalCoordinateSystemSpeedMax'))
            p['velocity'] = [rng.uniform(a, b) for a, b in zip(lo, hi)]
        elif kind == 'C_INIT_PositionOffset':
            lo, hi = vector(op.get('m_OffsetMin')), vector(op.get('m_OffsetMax'))
            p['offset'] = [v+rng.uniform(a, b) for v, a, b in zip(p['offset'], lo, hi)]
        elif kind == 'C_INIT_RandomColor':
            mix = rng.random()
            p['color'] = [(a+(b-a)*mix)/255 for a, b in zip(op.get('m_ColorMin', [255]*3)[:3], op.get('m_ColorMax', [255]*3)[:3])]
        elif kind == 'C_INIT_RandomSequence':
            p['sequence'] = rng.randint(op.get('m_nSequenceMin', 0), op.get('m_nSequenceMax', 0))
    if p['life'] <= 0 or p['radius'] < 0:
        raise UnsupportedEffect('Non-positive lifetime or negative radius')
    return p


def layer_particles(definition, instance, tick_rate, clip_end):
    classification = classify_layer(definition)
    seed = int(hashlib.sha256((str(instance['id'])+instance['resource']).encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    emitter = definition['m_Emitters'][0]
    count = round(scalar(emitter.get('m_nParticlesToEmit'), rng, 1))
    if not 0 <= count <= min(512, definition.get('m_nMaxParticles', 512)):
        raise UnsupportedEffect('Instantaneous particle count exceeds bounded renderer capacity')
    particles = []
    ribbon = classification['kind'] == 'recorded_ring_ribbon'
    for index in range(1 if ribbon else count):
        p = ribbon_particle(definition, instance, tick_rate, clip_end) if ribbon else initialize_particle(definition, rng)
        p.update(index=index, seed=str(seed), born_tick=instance['created_tick'])
        stop = instance.get('destroy_tick')
        if ribbon:
            pass
        elif classification['kind'] == 'attachment_charge':
            p['end_tick'] = stop+max(1, tick_rate*.02) if stop is not None else clip_end+1
        else:
            p['end_tick'] = p['born_tick'] + p['life']*tick_rate
        if instance.get('destroy_immediately') and stop is not None:
            p['end_tick'] = min(p['end_tick'], stop)
        p['renderers'] = []
        for render in classification['renderers']:
            settings = {'definition': render,
                        'radius_scale': scalar(render.get('m_flRadiusScale'), rng, 1),
                        'alpha_scale': scalar(render.get('m_flAlphaScale'), rng, 1),
                        'strength': scalar(render.get('m_flOverbrightFactor'), rng, 1),
                        'uv_controls': []}
            if ribbon:
                settings['ribbon_instance'] = instance['id']
            for tex in render['m_vecTexturesInput']:
                controls = tex.get('m_TextureControls', {})
                settings['uv_controls'].append({axis: scalar(controls.get(key), rng, default)
                    for axis, key, default in [('scale_u', 'm_flFinalTextureScaleU', 1),
                                               ('scale_v', 'm_flFinalTextureScaleV', 1),
                                               ('offset_u', 'm_flFinalTextureOffsetU', 0),
                                               ('offset_v', 'm_flFinalTextureOffsetV', 0)]})
            p['renderers'].append(settings)
        particles.append(p)
    return particles


def attachment_binding(instance):
    updates = [e['data']['update_particle_ent'] for e in instance.get('events', [])
               if 'update_particle_ent' in e.get('data', {})
               and e['data']['update_particle_ent'].get('control_point', 0) == 0]
    if not updates:
        raise UnsupportedEffect('No recorded control-point 0 entity attachment')
    unique = {(v.get('entity_handle'), v.get('attach_type'), v.get('attachment')) for v in updates}
    if len(unique) != 1:
        raise UnsupportedEffect('Control-point attachment changes during particle lifetime')
    value = updates[0]
    if value.get('attach_type') != 5 or not value.get('attachment', 0):
        raise UnsupportedEffect('Only recorded point-follow attachment type 5 is implemented')
    return value


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.part')
    temp.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    temp.replace(path)


def prepare_installed_effects(segment_path, pack_dir, provider, decoder, log=print, attachments_only=False):
    from .effect_assets import EffectAssetAdapter
    from .effect_lifetimes import maximum_effect_age, continuous_creation_clock
    segment_path, pack_dir = Path(segment_path).resolve(), Path(pack_dir).resolve()
    segment = json.loads(segment_path.read_text(encoding='utf-8'))
    out = pack_dir/'effects'
    adapter = EffectAssetAdapter(decoder)
    instances = segment.get('particle_instances', [])
    report = {'schema_version': 1, 'status': 'partial', 'network_instances': len(instances),
              'resolved_resource_instances': sum(bool(i.get('resource')) for i in instances),
              'unresolved_resource_instances': sum(not i.get('resource') for i in instances),
              'prepared_layers': [], 'unsupported': [], 'attachments': {}, 'textures': {},
              'limitations': ['This adapter supports a subset of attachment-locked sprites/trails and recorded-control-point ring ribbons; counts show what was actually prepared.',
                'Random draw order/seed, biased randomness, sprite roll, trail response and source bloom are approximated.',
                'No generic substitute is created for unsupported operators, atlas packing, beam or collision effects.']}
    from .model_tracks import logical_entities
    models = logical_entities(segment['entities'])
    source_models = {}
    for p in provider.resources:
        if p.endswith('.vmdl_c'):
            source_models.setdefault(str(resource_id(p[:-2])), p)
    defs, definition_errors, table_cache, texture_cache = {}, {}, {}, {}
    def get_definition(resource):
        if resource in definition_errors:
            raise UnsupportedEffect(definition_errors[resource])
        if resource not in defs:
            try:
                defs[resource] = decoder.dump(resource, provider)
            except (ConversionError, GameAssetError, OSError, ValueError) as exc:
                definition_errors[resource] = str(exc)
                raise
        return defs[resource]
    def prepare_binding(instance, binding):
        handle = binding.get('entity_handle')
        entity_id = instance.get('entity_id')
        if handle is not None:
            exact = f'{int(handle)&0x3fff}:{int(handle)>>14}'
            if exact in models:
                entity_id = exact
            else:
                raise UnsupportedEffect('Attachment target is not an exported exact-serial hero')
        if entity_id not in models:
            raise UnsupportedEffect('Particle target is not an exported exact-serial hero')
        if models[entity_id].get('model_tracks'):
            raise UnsupportedEffect('Particle attachments across recorded model changes are not yet qualified')
        root_resource = instance.get('resource')
        if not root_resource:
            raise UnsupportedEffect('Particle resource ID unresolved')
        model = source_models.get(str(models[entity_id].get('model_handle')))
        if not model:
            raise UnsupportedEffect('Attachment model resource ID unresolved')
        if model not in table_cache:
            table_cache[model] = adapter.attachments(model, provider)
        table = table_cache[model]
        index = int(binding['attachment'])
        if index < 1 or index > len(table):
            raise UnsupportedEffect('Recorded attachment index absent from installed model')
        entry = table[index-1]
        if entry.get('m_nInfluences', 1) != 1 or entry.get('m_influenceWeights', [1])[0] != 1:
            raise UnsupportedEffect('Weighted attachment blending is not implemented')
        if entry.get('m_bIgnoreRotation') or any(entry.get('m_bInfluenceRootTransform', [])):
            raise UnsupportedEffect('Special attachment root/rotation behavior')
        attachment_key = f'{entity_id}/{index}'
        report['attachments'][attachment_key] = {'entity_id': entity_id, 'index': index,
                                                   'source_model': model, 'definition': entry}
        return entity_id, index, entry, model

    age_bounds, expired_resources, retained_instances = {}, {}, []
    prepared = []
    for instance_number, instance in enumerate(instances):
        if attachments_only:
            retained_instances.append(instance)
            if instance.get('entity_id') not in models or not segment['start_tick']<=instance['created_tick']<=segment['end_tick']:
                continue
        if instance_number and instance_number % 2000 == 0:
            log(f'Checking particle lifetimes and supported layers: {instance_number:,}/{len(instances):,}')
        resource = instance.get('resource')
        if (not attachments_only and resource and instance['created_tick'] < segment['start_tick']
                and continuous_creation_clock(instance)):
            if resource not in age_bounds:
                age_bounds[resource] = maximum_effect_age(resource, get_definition)
            bound = age_bounds[resource]
            # Keep one simulation tick beyond the analytic bound for emission
            # quantization. No unknown or continuous lifetime gets a cutoff.
            if bound is not None and instance['created_tick']+(bound*segment['tick_rate'])+1 < segment['start_tick']:
                expired_resources[resource] = expired_resources.get(resource, 0)+1
                continue
        if not attachments_only:retained_instances.append(instance)
        try:
            if attachments_only:
                # Inventory all recorded point attachments. Lifecycle/CP ownership
                # is validated by the simulator before any instance can render.
                bindings = [event['data']['update_particle_ent'] for event in instance.get('events', [])
                            if event.get('type') == 'UPDATE_ENT'
                            and event.get('data', {}).get('update_particle_ent', {}).get('attach_type') == 5]
                if not bindings:
                    raise UnsupportedEffect('No recorded point-follow attachment')
                for binding in bindings:
                    prepare_binding(instance, binding)
                continue
            binding = attachment_binding(instance)
            entity_id, index, entry, model = prepare_binding(instance, binding)
            root_resource = instance.get('resource')
            queue, seen = [(root_resource, 0)], set()
            while queue:
                resource, delay = queue.pop(0)
                if resource in seen:
                    continue
                seen.add(resource)
                if len(seen) > 256:
                    raise UnsupportedEffect('Particle child tree exceeds 256 resources')
                try:
                    definition = get_definition(resource)
                    for child in definition.get('m_Children', []):
                        if child.get('m_bDisableChild'):
                            continue
                        if child.get('m_bEndCap') or child.get('m_nEndCapState'):
                            report['unsupported'].append({'instance': instance['id'], 'resource': child.get('m_ChildRef'), 'reason': 'Child endcap scheduling'})
                            continue
                        if child.get('m_ChildRef'):
                            queue.append((child['m_ChildRef'], delay+float(child.get('m_flDelay', 0))))
                    classification = classify_layer(definition)
                    # Earlier roots may already have ceased emitting. Individual
                    # lifetimes below determine whether their tail overlaps.
                    layer_instance = dict(instance, resource=resource, entity_id=entity_id,
                                          created_tick=instance['created_tick']+delay*segment['tick_rate'])
                    particles = layer_particles(definition, layer_instance, segment['tick_rate'], segment['end_tick'])
                    particles = [p for p in particles if p['end_tick'] > segment['start_tick'] and p['born_tick'] <= segment['end_tick']]
                    if not particles:
                        continue
                    for renderer in classification['renderers']:
                        for texture in renderer['m_vecTexturesInput']:
                            name = texture.get('m_hTexture')
                            if not name:
                                continue
                            if name not in texture_cache:
                                directory, descriptor = adapter.texture(name, provider)
                                dest = out/'textures'/directory.name
                                shutil.copytree(directory, dest, dirs_exist_ok=True)
                                texture_cache[name] = dict(descriptor, directory=dest.relative_to(out).as_posix())
                            descriptor = texture_cache[name]
                            for p in particles:
                                sequence = descriptor['sequences'].get(str(p['sequence']))
                                # Non-sheets use their one image independently of
                                # the particle's sequence number for another layer.
                                if sequence is None and not descriptor.get('is_sprite_sheet', len(descriptor['sequences']) > 1):
                                    sequence = descriptor['sequences'].get('0')
                                if not sequence or sequence['status'] != 'supported':
                                    raise UnsupportedEffect('Unsupported or absent texture sequence: '+name+' #'+str(p['sequence']))
                    layer = {'instance': layer_instance, 'attachment': attachment_key, 'resource': resource,
                             'definition': definition, 'kind': classification['kind'], 'particles': particles,
                             'omissions': classification['omissions']}
                    prepared.append(layer)
                    report['prepared_layers'].append({'instance': instance['id'], 'resource': resource,
                                                      'particles': len(particles), 'omissions': classification['omissions']})
                except (UnsupportedEffect, ConversionError, GameAssetError, OSError, ValueError, KeyError) as exc:
                    report['unsupported'].append({'instance': instance['id'], 'resource': resource, 'reason': str(exc)})
        except (UnsupportedEffect, ConversionError, GameAssetError, OSError, ValueError, KeyError) as exc:
            report['unsupported'].append({'instance': instance['id'], 'resource': instance.get('resource'), 'reason': str(exc)})
    # Long clips retain natural tails for lifetime-aware culling. Aggregate
    # repeated unsupported effects rather than producing thousands of identical
    # diagnostics or obscuring the reason behind a generic success message.
    grouped = {}
    for item in report['unsupported']:
        key = (item.get('resource'), item['reason'])
        if key not in grouped:
            grouped[key] = dict(item, occurrences=0, instance_examples=[])
        group = grouped[key]
        group['occurrences'] += 1
        if len(group['instance_examples']) < 3:
            group['instance_examples'].append(item['instance'])
    report['unsupported_occurrences'] = len(report['unsupported'])
    report['unsupported'] = list(grouped.values())
    report['textures'] = texture_cache
    report['prepared_layer_count'] = len(prepared)
    report['prepared_particle_count'] = sum(len(p['particles']) for p in prepared)
    effect_preparation_status(report)
    if attachments_only:
        report.update(status='prepared',backend='recorded_simulation_attachments',
                      reason='Attachments prepared; simulation and visual coverage are determined in Blender.',
                      limitations=['Attachment preparation only; simulation and rendering have independent coverage.'])
        report.pop('reason_code',None)
    report['authored_expiry_pruning'] = {'removed_instances': sum(expired_resources.values()),
        'retained_instances': len(retained_instances), 'expired_by_resource': expired_resources,
        'proven_maximum_age_seconds': {k: v for k, v in age_bounds.items() if v is not None},
        'unknown_lifetimes': 'Retained; no guessed preroll horizon is applied'}
    manifest = {'schema_version': 1, 'layers': prepared, 'attachments': report['attachments'],
                'textures': texture_cache, 'limitations': report['limitations'],
                'coverage': preparation_summary(report)}
    _write(out/'effects_manifest.json', manifest)
    _write(out/'effects_report.json', report)
    segment['effects'] = {'manifest_file': str(out/'effects_manifest.json'),
                          'report_file': str(out/'effects_report.json'), 'status': report['status']}
    segment.setdefault('coverage', {})['particles'] = report['status']
    segment['particle_instances'] = retained_instances
    _write(segment_path, segment)
    if attachments_only:
        log(f"Effect attachments prepared: {len(report['attachments'])}; recorded simulation follows scene assembly.")
    else:
        log(f"Effects prepared: {len(prepared)} supported layers; {len(report['unsupported'])} unsupported or unavailable layer records.")
    if report.get('reason'):
        log(('WARNING: ' if report['status'] == 'unavailable' else '') + report['reason'])
    return report
