import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from agr.asset_converter import ConversionError
from agr.effect_assets import attachment_values, parse_mks, png_info, _texture_descriptor, _kv3_blocks


def png(path, width=2, height=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b'\x89PNG\r\n\x1a\n'
    raw += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    raw += chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * (4 * width)) * height))
    raw += chunk(b'IEND', b'')
    Path(path).write_bytes(raw)


class EffectAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_source_mks_alias_keeps_sequence_index_and_image_crop(self):
        png(self.root / 'glow_seq0.png', 4, 2)
        data = parse_mks('// VRF\nsequence 0\nLOOP\nframe glow_seq0.png 1\nsequence 7\nframe glow_seq0.png 1\n', self.root)
        self.assertEqual(list(data), ['0', '7'])
        self.assertEqual(data['7']['frames'][0]['image'], 'glow_seq0.png')
        self.assertEqual(data['0']['frames'][0]['width'], 4)
        self.assertEqual(data['7']['status'], 'supported')
        self.assertTrue(data['0']['loop'])

    def test_animated_or_split_channel_atlas_is_not_claimed_supported(self):
        png(self.root / 'one.png')
        animated = parse_mks('sequence 0\nframe one.png .1\nframe one.png .2\n', self.root)
        self.assertEqual(animated['0']['status'], 'unsupported')
        split = parse_mks('packmode rgb+a\nsequence-rgb 0\nframe one.png 1\nsequence-a 1\nframe one.png 1\n', self.root)
        self.assertTrue(all(s['status'] == 'unsupported' for s in split.values()))

    def test_named_sequences_preserve_indices_and_support_limits(self):
        png(self.root / 'one.png')
        data = parse_mks('sequence 0\nname muzzle_flash\nframe one.png 1\n'
                         'sequence 7\nname "smoke cloud"\nframe one.png .1\nframe one.png .2\n'
                         'sequence-rgb 8\nname separate color\nframe one.png 1\n', self.root)
        self.assertEqual(list(data), ['0', '7', '8'])
        self.assertEqual(data['0']['name'], 'muzzle_flash')
        self.assertEqual(data['0']['status'], 'supported')
        self.assertEqual(data['7']['name'], 'smoke cloud')
        self.assertEqual(data['7']['status'], 'unsupported')
        self.assertEqual(data['8']['name'], 'separate color')
        self.assertEqual(data['8']['status'], 'unsupported')

    def test_invalid_sequence_names_and_unknown_metadata_fail(self):
        for text in ('name orphan', 'sequence 0\nname', 'sequence 0\nname ""',
                     'sequence 0\nname first\nname second', 'sequence 0\nunknown metadata'):
            with self.subTest(text=text), self.assertRaises(ConversionError):
                parse_mks(text, self.root)

    def test_texture_report_supports_some_sequences_but_reports_others(self):
        png(self.root / 'one.png')
        (self.root / 'texture.mks').write_text('sequence 0\nframe one.png 1\nsequence 1\nframe one.png 1\nframe one.png 1\n')
        descriptor = _texture_descriptor(self.root, 'materials/test.vtex_c')
        self.assertTrue(descriptor['atlas'])
        self.assertEqual(descriptor['status'], 'partial')
        self.assertEqual(descriptor['sequences']['1']['status'], 'unsupported')

    def test_missing_or_unsafe_mks_image_fails(self):
        for name in ('../out.png', '/out.png', 'C:/out.png', 'nul.png', 'missing.png'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_mks(f'sequence 0\nframe {name} 1\n', self.root)

    def test_truncated_or_corrupted_png_fails(self):
        p = self.root / 'one.png'
        png(p)
        original = p.read_bytes()
        for contents in (original[:-1], original[:30] + b'\x00' + original[31:], original + b'junk'):
            p.write_bytes(contents)
            with self.assertRaises(ConversionError):
                png_info(p)

    def test_mks_duplicate_index_and_invalid_duration_fail(self):
        png(self.root / 'one.png')
        for content in ('sequence 0\nsequence 00', 'sequence 0\nframe one.png nan',
                        'sequence 0\nframe one.png -1', 'unknown 0'):
            with self.subTest(content=content), self.assertRaises(ConversionError):
                parse_mks(content, self.root)

    def test_static_raw_png_output_and_ambiguous_array(self):
        png(self.root / 'texture.vtex')
        self.assertEqual(_texture_descriptor(self.root, 'a.vtex_c')['status'], 'supported')
        (self.root / 'texture.vtex').unlink()
        png(self.root / 'array_z001.png')
        png(self.root / 'array_z002.png')
        self.assertEqual(_texture_descriptor(self.root, 'a.vtex_c')['status'], 'unsupported')

    def test_attachment_order_retained_with_unused_middle_slot(self):
        data = {'m_attachments': [{'key': key, 'value': {'m_name': key}} for key in ('z_muzzle', 'unused', 'a_muzzle')]}
        self.assertEqual([a['m_name'] for a in attachment_values(data)], ['z_muzzle', 'unused', 'a_muzzle'])

    def test_multiple_mdat_dumps_are_separated(self):
        header = '<!-- kv3 encoding:text:version{} format:generic:version{} -->\n'
        data = list(_kv3_blocks('File name\n' + header + '{ m_attachments = [] }\nMDAT\n' + header +
                               '{ m_name = "curly { name }" }\nDone\n'))
        self.assertEqual(data, [{'m_attachments': []}, {'m_name': 'curly { name }'}])


if __name__ == '__main__':
    unittest.main()
