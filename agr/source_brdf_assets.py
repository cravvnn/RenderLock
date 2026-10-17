"""Pinned installed BRDF layer extraction, qualified against VRF in C19.

This is deliberately not a general texture parser: the complete compiled source
hash pins its 1048-byte header and uncompressed three-layer RGBA16F payload.
"""
import hashlib
from pathlib import Path
import numpy as np
from .asset_converter import ConversionError,sha

RESOURCE='textures/dev/brdf_lut.vtex_c'
SOURCE_SHA256='c51ff7434edec5f760f3430daa19da438ca1b620aa5f729590bb56e3b7bec72e'
LAYER_SHA256='882be63703ff64127d387fe0fddf9efc87428d360207299b1d430aec32137a5e'


def extract_layer(source):
    data=Path(source).read_bytes()
    if len(data)!=99352 or hashlib.sha256(data).hexdigest()!=SOURCE_SHA256:
        raise ConversionError('Installed BRDF LUT changed; requalification required')
    layer=data[1048+32768:1048+65536]
    if hashlib.sha256(layer).hexdigest()!=LAYER_SHA256:
        raise ConversionError('Qualified BRDF layer layout changed')
    return layer


def read_layer(path):
    data=Path(path).read_bytes()
    if len(data)!=32768 or hashlib.sha256(data).hexdigest()!=LAYER_SHA256:
        raise ConversionError('Source BRDF layer payload changed')
    return np.frombuffer(data,dtype='<f2').astype(np.float32).reshape(64,64,4)


def prepare_brdf_assets(provider,decoder):
    resolved=provider.resolve(RESOURCE)
    if resolved.sha256!=SOURCE_SHA256:
        raise ConversionError('Installed BRDF LUT changed; requalification required')
    def build(temp):
        (temp/'brdf-layer1.rgba16f').write_bytes(extract_layer(resolved.local_path))
    directory=decoder._entry('source_BRDF_layer1_v1',{RESOURCE:resolved.sha256},
        {'adapter_sha256':sha(Path(__file__))},build)
    path=directory/'brdf-layer1.rgba16f';read_layer(path)
    return dict(path=str(path),sha256=LAYER_SHA256,source_resource=RESOURCE,
        source_sha256=SOURCE_SHA256,size=[64,64],layer=1,format='rgba16f_le_source_rows')


def validate_brdf_asset(row,manifest_dir):
    expected=dict(sha256=LAYER_SHA256,source_resource=RESOURCE,source_sha256=SOURCE_SHA256,
                  size=[64,64],layer=1,format='rgba16f_le_source_rows')
    if not isinstance(row,dict) or type(row.get('layer')) is not int or any(row.get(k)!=v for k,v in expected.items()):
        raise ConversionError('Unqualified Source BRDF asset')
    path=Path(row['path'])
    if not path.is_absolute():path=Path(manifest_dir)/path
    read_layer(path)
    return path.resolve()
