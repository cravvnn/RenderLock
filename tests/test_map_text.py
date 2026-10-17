"""Map text regression, strict failure reporting and separate cache identity."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agr.asset_converter import AssetConverter, ConversionError, sha
from agr.map_data import dump_map_data, map_data_identity
from agr.map_text import MapTextError, parse_map_kv3
from agr.vendor.kv3_text import parse_kv3


HEADER = '<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->\n'


def document(body):
    return HEADER + '{ ' + body + ' }\n'


def test_multiline_regression_reproduces_report_and_preserves_nearby_model():
    source = document('description = """\nA source map note\nwith two lines\n"""\n'
                      'model = resource:"models/architecture/door.vmdl"')
    with pytest.raises(json.JSONDecodeError, match=r'Invalid control character at: line 1 column 2 \(char 1\)'):
        parse_kv3(source)
    assert parse_map_kv3(source) == {'description': 'A source map note\nwith two lines',
                                     'model': 'models/architecture/door.vmdl'}


@pytest.mark.parametrize(('body', 'expected'), [
    ('\nline one\nline two\n', 'line one\nline two'),
    ('\r\nline one\r\nline two\r\n', 'line one\r\nline two'),
    ('\n\nleading and trailing blank lines\n\n', '\nleading and trailing blank lines\n'),
    ('\nC:\\maps\\notes\n// not a comment\n#[ AB CD ]\n', 'C:\\maps\\notes\n// not a comment\n#[ AB CD ]'),
    ('\nA "quote" and \\""" escaped delimiter\n', 'A "quote" and \\""" escaped delimiter'),
    ('\n\n', ''),
])
def test_multiline_reader_matches_cli_raw_string_contract(body, expected):
    assert parse_map_kv3(document('text = """' + body + '"""')) == {'text': expected}


def test_single_strings_flags_bytes_comments_and_numbers():
    source = document(r''' // leading comment
        "quoted key\'" = "line\nquote\"tab\tbackslash\\end"
        /* not a string */ values = [false, null, 17, -.25, 3.4e2,]
        source = resource_name:"materials/test.vmat"
        bytes = #[ 00 01 FF 7f ]
        literal = "#[ AB CD ] // unchanged"
        .metadata.value = "bare dotted key"
    ''')
    expected = {"quoted key'": 'line\nquote"tab\tbackslash\\end',
                'values': [False, None, 17, -.25, 340],
                'source': 'materials/test.vmat', 'bytes': [0, 1, 255, 127],
                'literal': '#[ AB CD ] // unchanged', '.metadata.value': 'bare dotted key'}
    assert parse_map_kv3(source) == expected


def test_multiline_escaped_quote_parity_does_not_consume_following_property():
    # Two backslashes do not escape a delimiter in the reference token reader.
    source = document('note = """\nends in two slashes\\\\""" model = "models/door.vmdl"')
    assert parse_map_kv3(source) == {'note': 'ends in two slashes\\\\', 'model': 'models/door.vmdl'}


@pytest.mark.parametrize('body', [
    'x = """inline opening\n"""', 'x = """\nmissing closing',
    'x = "unterminated',
    r'x = "invalid\qescape"', 'x = #[ 0 ]', 'x = #[ GG ]', 'x = #[ 00',
    'x = [1,,2]', 'x = [1,2', 'x = { a = 1', 'x = 1 x = 2',
    'x = { a = 1 a = 2 }', 'x = undefined', 'x = 1e9999',
    'x = 1 /* unterminated', 'x = 1 } trailing = {',
])
def test_malformed_data_remains_rejected_with_original_position(body):
    source = 'CLI diagnostic preamble\n' + document(body)
    with pytest.raises(MapTextError) as error:
        parse_map_kv3(source)
    assert 0 <= error.value.offset <= len(source)
    assert error.value.line >= 3 and error.value.column >= 1
    assert len(error.value.excerpt) <= 200


def test_headers_root_and_trailing_documents_are_rejected():
    for source in ('{}', '<!-- kv3 bad header --> {}', HEADER + '[]', document('x = 1') + '{}'):
        with pytest.raises(MapTextError):
            parse_map_kv3(source)


def test_kv3_raw_controls_are_not_json_and_are_preserved_exactly():
    source = document('note = "\fcontrol first\rpreserve CR\vVT\tTAB" model = "models/door.vmdl"')
    with pytest.raises(json.JSONDecodeError, match=r'Invalid control character at: line 1 column 2 \(char 1\)'):
        parse_kv3(source)
    assert parse_map_kv3(source) == {'note': '\fcontrol first\rpreserve CR\vVT\tTAB',
                                     'model': 'models/door.vmdl'}


def decoder_fixture(tmp_path):
    path = tmp_path / 'map.vents_c'
    path.write_bytes(b'Fixture compiled entity bytes')
    resolved = SimpleNamespace(local_path=path, sha256=sha(path))
    provider = SimpleNamespace(resolve=lambda resource: resolved)
    decoder = AssetConverter.__new__(AssetConverter)
    decoder.root = tmp_path / 'derived'; decoder.root.mkdir()
    decoder.executable = tmp_path / 'Source2Viewer-CLI.exe'
    decoder.identity = {'hero_converter_identity': 'unchanged'}
    decoder.stats = {'cache_hits': 0, 'conversions': 0}
    return decoder, provider


def test_production_map_adapter_cache_reuse_corruption_and_legacy_isolation(tmp_path, monkeypatch):
    decoder, provider = decoder_fixture(tmp_path)
    source = document('note = """\nfirst\nsecond\n""" model = "models/door.vmdl"').encode()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[-2:] == ['-b', 'DATA']
        return SimpleNamespace(returncode=0, stdout=source, stderr=b'')
    monkeypatch.setattr('agr.map_data.subprocess.run', run)
    # An older generic DATA result must never hide a grammar upgrade.
    resource = 'maps/dl_midtown/entities/default_ents.vents_c'
    legacy = decoder._entry('DATA', {resource: provider.resolve(resource).sha256}, {},
                            lambda temp: (temp / 'data.json').write_text('{"legacy":true}'))
    legacy_bytes = (legacy / 'conversion.json').read_bytes()
    first = dump_map_data(decoder, resource, provider)
    assert first == {'note': 'first\nsecond', 'model': 'models/door.vmdl'}
    assert dump_map_data(decoder, resource, provider) == first and len(calls) == 1
    assert decoder.identity == {'hero_converter_identity': 'unchanged'}
    assert (legacy / 'conversion.json').read_bytes() == legacy_bytes
    record_path = next(p for p in decoder.root.glob('*/conversion.json')
                       if json.loads(p.read_text())['descriptor']['operation'] == 'map_DATA')
    record = json.loads(record_path.read_text())
    assert record['descriptor']['settings'] == map_data_identity()
    (record_path.parent / 'data.json').write_text('{"corrupted": true}')
    assert dump_map_data(decoder, resource, provider) == first and len(calls) == 2


def test_failure_keeps_exact_stdout_location_and_allows_clean_retry(tmp_path, monkeypatch):
    decoder, provider = decoder_fixture(tmp_path)
    resource = 'maps/dl_midtown/entities/default_ents.vents_c'
    source = ('CLI header\n' + document('model = "unterminated')).encode()
    output = SimpleNamespace(returncode=0, stdout=source, stderr=b'CLI stderr bytes\r\n')
    monkeypatch.setattr('agr.map_data.subprocess.run', lambda *a, **k: output)
    with pytest.raises(ConversionError, match='Map DATA diagnostics retained at') as error:
        dump_map_data(decoder, resource, provider)
    assert resource in str(error.value)
    diagnostics = list((tmp_path / 'map-diagnostics').glob('*/error.json'))
    assert len(diagnostics) == 1
    report = json.loads(diagnostics[0].read_text())
    assert report['resource'] == resource
    assert report['parse_location']['line'] == 3
    assert (diagnostics[0].parent / 'DATA.stdout.txt').read_bytes() == source
    assert (diagnostics[0].parent / 'DATA.stderr.txt').read_bytes() == output.stderr
    assert not list(decoder.root.glob('*/conversion.json'))
    output.stdout = document('model = "models/door.vmdl"').encode()
    assert dump_map_data(decoder, resource, provider) == {'model': 'models/door.vmdl'}


def test_nonzero_decoder_and_invalid_utf8_remain_failures(tmp_path, monkeypatch):
    decoder, provider = decoder_fixture(tmp_path)
    output = SimpleNamespace(returncode=7, stdout=b'partial DATA', stderr=b'failed decoding resource')
    monkeypatch.setattr('agr.map_data.subprocess.run', lambda *a, **k: output)
    with pytest.raises(ConversionError, match='code 7'):
        dump_map_data(decoder, 'world.vwrld_c', provider)
    output.returncode, output.stdout = 0, b'\xff'
    with pytest.raises(ConversionError, match='utf-8'):
        dump_map_data(decoder, 'world.vwrld_c', provider)
    assert not list(decoder.root.glob('*/conversion.json'))


def test_converter_exception_log_stays_in_cache_and_survives_failed_transaction(tmp_path, monkeypatch):
    decoder, provider = decoder_fixture(tmp_path)
    working_dirs = []
    exact_log = b'Unknown KVType: 26 (0x1A)\r\nfull converter failure\r\n'
    def run(command, **kwargs):
        cwd = Path(kwargs['cwd'])
        assert cwd.is_relative_to(decoder.root)
        working_dirs.append(cwd)
        (cwd / 'exceptions.txt').write_bytes(exact_log)
        return SimpleNamespace(returncode=2, stdout=b'', stderr=b'unsupported binary type')
    monkeypatch.setattr('agr.map_data.subprocess.run', run)
    with pytest.raises(ConversionError, match='code 2'):
        dump_map_data(decoder, 'world.vwrld_c', provider)
    log = next((tmp_path / 'map-diagnostics').glob('*/exceptions.txt'))
    assert log.read_bytes() == exact_log
    assert len(working_dirs) == 1 and not working_dirs[0].exists()
