"""Explicit admission for one qualified dense read profile.

No process reads, writes, caches or cross-call state. Legacy callers retain
4 MiB; a boolean or a caller-chosen numeric limit cannot expand admission.
The normalized cleanup proof is content addressed in the reference's source.
The normal closed-case audit remains responsible for replaying raw native data.
"""
import hashlib
import json
from .replay_capture import canonical_sha256
from .observed_slide_profile import digest

POLICY='agr_dense_material_v5_read_profile_6860_v1'
NATIVE='9f202cf81771b08408930413c8447b74ab5369c3b330eb1f24bdf3a32299f262'
OBSERVER='d6872f7db427718cd58e9dd80e0c250ea296927249a6d81a2f430aafa880a75e'
READER='d092d75b5f407c75132063555b18146a6dd1ecee671241398f165788e7a0c508'
CONTROLLER='3fcf49466604b18be847d1d1bb0b44e057feeca9bcbab48a4c3a870fbf62649b'
CLIENT='fadc636d762d4a6139655b6fd54747826097162ce719e6add307429eabb4f3c1'
HELPER='0d5127f897ab387aa85fd0e050dc4b74f047a39b3a1ee9e9566ca1ac5bf08101'
LIMITS=dict(external=16*1024**2,native=1024**2,helper=3*1024**2)


def require(value,message):
    if not value:raise ValueError('Dense capture read profile: '+message)


def read_limit(report,source,cleanup):
    """Require exact composed provenance before accepting the explicit profile."""
    if 'capture_read_profile' not in cleanup:return 4*1024**2
    require(source.get('cleanup_sha256')==canonical_sha256(cleanup),
            'normalized owned closure proof digest differs')
    p=cleanup['capture_read_profile']
    expected={'policy','native_sha256','observer_sha256','field_reader_sha256',
              'controller_sha256','client_sha256','helper_sha256','freeze_sha256',
              'report_sha256','archive_sha256','release_sha256','limits','final_external_bytes',
              'final_native_bytes','final_helper_bytes','ticks','material_frames'}
    require(type(p)is dict and set(p)==expected,'profile fields differ')
    require(p['policy']==POLICY and p['native_sha256']==NATIVE
            and p['observer_sha256']==source.get('observer_sha256')==OBSERVER
            and p['field_reader_sha256']==source.get('field_reader_sha256')==READER
            and p['controller_sha256']==CONTROLLER and p['client_sha256']==CLIENT
            and p['helper_sha256']==HELPER and p['limits']==LIMITS,
            'unqualified reader/controller/native/helper or limits')
    require(all(digest(p[k])for k in('freeze_sha256','report_sha256','archive_sha256','release_sha256'))
            and p['freeze_sha256']==source.get('freeze_sha256')
            and p['report_sha256']==source.get('report_sha256')==cleanup.get('report_sha256')
            and p['archive_sha256']==cleanup.get('archive_sha256'), 'closure/source identity differs')
    require(cleanup.get('status')=='PASS_EXACT_OWNED_DEPLOYMENT_ARCHIVED'
            and all(cleanup.get(k)is True for k in('source_absent','runtime_restored','owned_native_removed_from_game')),
            'exact owned cleanup unavailable')
    require(report.get('phase')=='closed' and report.get('workflow_passed')is True
            and report.get('exact_target_coverage')is True and report.get('exit_code')==0
            and not report.get('normal_deadline_reached') and not report.get('error'),
            'complete closed workflow required')
    # This profile pins the compact writer. Bind the entire input object to the
    # actual report bytes; callers cannot edit counters/arrays while retaining
    # a valid-looking closure receipt or select an arbitrary larger budget.
    encoded=json.dumps(report,separators=(',',':'),default=str).encode('utf8')
    require(len(encoded)<=128*1024**2 and hashlib.sha256(encoded).hexdigest()==p['report_sha256'],
            'actual compact report payload differs')
    require(p['ticks']==321 and len(report.get('seeks',[]))==321
            and [e.get('target')for e in report['seeks']]==list(range(17920,18241))
            and type(p['material_frames'])is int and 1<=p['material_frames']<=64
            and report.get('material_positive_frames')==p['material_frames'],
            'dense or material coverage differs')
    counters=dict(external=p['final_external_bytes'],native=p['final_native_bytes'],helper=p['final_helper_bytes'])
    require(all(type(v)is int and 0<v<=LIMITS[k]for k,v in counters.items()),
            'whole-run counter exceeds original cap')
    require(report.get('observer_read_bytes')==counters['external']
            and report.get('material_native_read_bytes')==counters['native'],
            'actual final observer counters differ')
    positive=[e['frame_cap']['dense_material']for e in report['seeks']
              if e['native_capture']['draw_material_inputs']['rows']]
    require(len(positive)==p['material_frames']
            and positive[-1]['ffi_actual_bytes']==counters['helper']
            and positive[-1]['native_bytes']==counters['native'], 'terminal helper/native counts differ')
    previous=0
    for e in report['seeks']:
        sample=e['native_capture'];value=sample['external_bytes']
        require(type(value)is int and previous<value<=LIMITS['external'],
                'attempted observer ledger not monotonic')
        previous=sample.get('draw_external_accounted_bytes',value)
        require(type(previous)is int and value<=previous<=LIMITS['external'],
                'material reads missing from observer ledger')
    require(previous==counters['external'],'last frame/final counter differs')
    return LIMITS['external']
