import copy
from types import SimpleNamespace
import pytest

from agr.asset_converter import ConversionError
from agr.source_region import verify_region_inputs, prepare_source_map


def fixture(tmp_path):
    world='maps/test/world.vwrld_c'
    records={world:{'archive':str(tmp_path/'map.vpk'),'sha256':'a'*64},
             'materials/test.vmat_c':{'archive':str(tmp_path/'game.vpk'),'sha256':'b'*64}}
    provider=SimpleNamespace(resource_identity=lambda name:{'resource_path':name,**records[name]})
    report={'loaded':list(records),'inputs':copy.deepcopy(records)}
    return world,provider,report


def test_actual_payload_and_precedence_match(tmp_path):
    world,provider,report=fixture(tmp_path)
    verify_region_inputs(report,provider,world,'a'*64)


@pytest.mark.parametrize('field,value',[('archive','other.vpk'),('sha256','c'*64)])
def test_same_name_wrong_payload_or_archive_rejected(tmp_path,field,value):
    world,provider,report=fixture(tmp_path)
    report['inputs']['materials/test.vmat_c'][field]=value
    with pytest.raises(ConversionError,match='identity/precedence'):
        verify_region_inputs(report,provider,world,'a'*64)


def test_incomplete_source_inventory_and_changed_world_rejected(tmp_path):
    world,provider,report=fixture(tmp_path)
    report['loaded'].pop()
    with pytest.raises(ConversionError,match='inconsistent'):verify_region_inputs(report,provider,world,'a'*64)
    report['loaded']=list(report['inputs'])
    with pytest.raises(ConversionError,match='world source changed'):verify_region_inputs(report,provider,world,'c'*64)
    del report['inputs'][world]
    with pytest.raises(ConversionError,match='inventory'):verify_region_inputs(report,provider,world,'a'*64)


def test_implicit_partial_preparation_rejected_before_work():
    with pytest.raises(ConversionError,match='explicit'):
        prepare_source_map({},None,None,probe=None,dotnet=None)
