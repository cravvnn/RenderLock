import json
from types import SimpleNamespace
import pytest

from agr.asset_converter import AssetConverter, ConversionError, sha
from agr.model_constraint_assets import prepare_model_constraints
from test_constraint_attachments import attachment, definition

HEADER='<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->\n'

def kv(value):
    return json.dumps(value).replace('\":', '\"=')


def fixture(tmp_path, monkeypatch):
    source=tmp_path/'model.vmdl_c';source.write_bytes(b'exact fixture model')
    decoder=AssetConverter.__new__(AssetConverter)
    decoder.root=tmp_path/'derived';decoder.root.mkdir()
    decoder.executable=tmp_path/'converter.exe'
    decoder.identity={'fixture':'model-converter-unchanged'}
    decoder.stats={'cache_hits':0,'conversions':0}
    provider=SimpleNamespace(resolve=lambda _:SimpleNamespace(local_path=source,sha256=sha(source)))
    keys=HEADER+'{ BoneConstraintList = '+kv([definition()])+' }'
    data=HEADER+'{ m_modelSkeleton = '+kv(dict(m_boneName=['driver','slave'],m_nParent=[-1,-1]))+' m_modelInfo = { m_keyValueText = '+json.dumps(keys)+' } }'
    tables=[attachment()];calls=[]
    def run(command, **kwargs):
        block=command[-1];calls.append(block)
        return SimpleNamespace(returncode=0,stderr='',stdout=data if block=='DATA' else HEADER+kv({'m_attachments':tables}))
    monkeypatch.setattr('agr.model_constraint_assets.subprocess.run',run)
    return decoder,provider,source,tables,calls


def test_source_bound_table_reuse_corruption_and_source_change(tmp_path,monkeypatch):
    decoder,provider,source,tables,calls=fixture(tmp_path,monkeypatch)
    first=prepare_model_constraints(decoder,'models/hero.vmdl',provider)
    assert first['attachments']==tables
    assert first['attachment_source']==dict(resource='models/hero.vmdl_c',sha256=sha(source))
    assert prepare_model_constraints(decoder,'models/hero.vmdl',provider)==first
    assert calls==['DATA','MDAT']
    entry=next(decoder.root.glob('*/constraints.json'))
    entry.write_text('{}')
    assert prepare_model_constraints(decoder,'models/hero.vmdl',provider)==first
    assert calls==['DATA','MDAT','DATA']  # Existing verified attachment entry reused.
    source.write_bytes(b'new exact fixture model')
    second=prepare_model_constraints(decoder,'models/hero.vmdl',provider)
    assert second['resource_sha256']!=first['resource_sha256'] and calls[-2:]==['DATA','MDAT']
    assert decoder.identity=={'fixture':'model-converter-unchanged'}
    record=json.loads((entry.parent/'conversion.json').read_text())
    assert {'model_constraints.py','model_constraint_math.py','effect_assets.py','map_text.py'} <= record['descriptor']['settings'].keys()


def test_ambiguous_embedded_table_refused(tmp_path,monkeypatch):
    decoder,provider,source,tables,calls=fixture(tmp_path,monkeypatch)
    tables.append(attachment())
    with pytest.raises(ValueError,match='Ambiguous constraint attachment'):
        prepare_model_constraints(decoder,'models/hero.vmdl',provider)
    assert not list(decoder.root.glob('*/constraints.json'))


def test_missing_attachment_bone_refused_before_metadata_publication(tmp_path,monkeypatch):
    decoder,provider,source,tables,calls=fixture(tmp_path,monkeypatch)
    tables[0]['m_influenceNames'][0]='missing'
    with pytest.raises(ValueError,match='influence bone'):
        prepare_model_constraints(decoder,'models/hero.vmdl',provider)
    assert not list(decoder.root.glob('*/constraints.json'))
