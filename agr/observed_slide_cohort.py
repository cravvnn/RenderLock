"""Pure same-capture check shared by optional slide render components."""
from .observed_slide_profile import digest

REFERENCE_KEYS = ('observed_slide_sprite_reference', 'observed_slide_trail_reference',
                  'observed_slide_light_reference', 'observed_slide_model_reference')


def validate_slide_cohort(runtime):
    reports = set()
    for key in REFERENCE_KEYS:
        reference = runtime.get(key)
        if reference is None:
            continue
        if not isinstance(reference, dict) or not isinstance(reference.get('capture'), dict):
            raise ValueError('Slide capture cohort reference is malformed: '+key)
        source = reference['capture'].get('source')
        if not isinstance(source, dict) or not digest(source.get('report_sha256')):
            raise ValueError('Slide capture cohort report identity is unavailable: '+key)
        reports.add(source['report_sha256'])
    if len(reports) > 1:
        raise ValueError('Slide components must share one capture cohort; mixed replay runs cannot be combined')
