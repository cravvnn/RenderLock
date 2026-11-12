"""Render settings for newly generated particle scenes; no material mutation."""
MIN_TRANSPARENT_BOUNCES = 128


def configure_particle_transparency(scene, effects):
    count = effects.get('objects', 0) if isinstance(effects, dict) else 0
    if scene.render.engine != 'CYCLES' or type(count) is not int or count <= 0:
        return dict(status='not_required')
    previous = scene.cycles.transparent_max_bounces
    scene.cycles.transparent_max_bounces = max(previous, MIN_TRANSPARENT_BOUNCES)
    return dict(status='configured', renderer='CYCLES', previous_bounces=previous,
                transparent_max_bounces=scene.cycles.transparent_max_bounces,
                scope='Transparency budget for overlapping particle surfaces; not Source pixel parity or an unlimited-overdraw guarantee')
