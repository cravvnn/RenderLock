"""Installed portrait decoding, isolation and the existing icon color path."""
import struct
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

from agr import gui_characters as characters, gui_theme
from agr.gui_portraits import decode_rgba_png, load_nurse_portrait


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload +
            struct.pack('>I', zlib.crc32(payload, zlib.crc32(kind)) & 0xffffffff))


def png(rows, filters):
    n = len(rows)
    previous = bytes(n*4)
    encoded = bytearray()
    for row, kind in zip(rows, filters):
        encoded.append(kind)
        for i, value in enumerate(row):
            left = row[i-4] if i >= 4 else 0
            above = previous[i]
            upper_left = previous[i-4] if i >= 4 else 0
            if kind == 0: predictor = 0
            elif kind == 1: predictor = left
            elif kind == 2: predictor = above
            elif kind == 3: predictor = (left+above)//2
            else:
                choices = (left, above, upper_left)
                distance = [abs(left+above-upper_left-x) for x in choices]
                predictor = choices[distance.index(min(distance))]
            encoded.append((value-predictor) % 256)
        previous = row
    header = struct.pack('>IIBBBBB', n, n, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', header)+
            chunk(b'IDAT', zlib.compress(bytes(encoded)))+chunk(b'IEND', b''))


def test_rgba_png_filters_and_partial_alpha_are_preserved():
    rows = [bytes((i*29 + j*37) % 256 for j in range(20)) for i in range(5)]
    side, actual = decode_rgba_png(png(rows, [0, 1, 2, 3, 4]))
    assert side == 5 and actual == b''.join(rows)


def test_corrupt_or_oversized_portrait_is_rejected():
    rows = [bytes(range(8)), bytes(range(8, 16))]
    data = bytearray(png(rows, [0, 4]))
    data[-5] ^= 1
    with pytest.raises(ValueError):
        decode_rgba_png(bytes(data))
    with pytest.raises(ValueError):
        decode_rgba_png(b'\x89PNG\r\n\x1a\n' + b'x' * (4*1024*1024))


def test_installed_icon_uses_the_existing_screen_color_and_resize_path(monkeypatch):
    monkeypatch.setattr(gui_theme, 'screen_pixels', lambda pixels: np.minimum(pixels, 140))
    raw = bytes([200, 100, 50, 128] * 4)
    characters.set_installed_portrait(87, (2, raw))
    try:
        source = characters._source(87, 1)
        assert np.allclose(source[0, 0], np.array([140, 100, 50, 255]) * (128/255/255), atol=1e-6)
        with pytest.raises(ValueError, match='designer portrait'):
            characters.set_installed_portrait(86, (2, raw))
    finally:
        characters.set_installed_portrait(87, None)


def test_loader_converts_only_installed_nurse_and_removes_owned_temporary_files(tmp_path, monkeypatch):
    import agr.game_assets as game_assets
    from agr import gui_portraits
    cache = tmp_path/'cache'
    exe = tmp_path/'Source2Viewer-CLI.exe'
    exe.touch()
    portrait = png([bytes(range(8)), bytes(range(8,16))], [0,4])
    calls = []
    class Provider:
        def __init__(self, game, cache_dir):
            self.cache_dir = cache
            self.archives = [SimpleNamespace(assert_unchanged=lambda: calls.append('stable'))]
        def resolve(self, resource):
            calls.append(resource)
            return SimpleNamespace(local_path=tmp_path/'installed.vtex_c')
    def run(argv, **kwargs):
        target = argv[argv.index('-o')+1]
        from pathlib import Path
        Path(target).with_suffix('.png').write_bytes(portrait)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(game_assets, 'GameAssetProvider', Provider)
    monkeypatch.setattr(gui_portraits.subprocess, 'run', run)
    assert load_nurse_portrait(tmp_path/'game', exe, cache) == (2, bytes(range(16)))
    assert calls == [gui_portraits.NURSE_RESOURCE, 'stable']
    assert not list((cache/'portrait-work').iterdir())


def test_async_portrait_discards_stale_results_and_uses_default_cache(monkeypatch, tmp_path):
    import queue
    from agr import gui, gui_portraits
    values = {'game_install': 'game-one', 'source2viewer': 'cli', 'asset_cache': ''}
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: SimpleNamespace(get=lambda key=key: values[key]) for key in values}
    cancelled, redraws, workers, requests, logs = [], [], [], [], []
    app.character_picker = SimpleNamespace(hero_ids={87}, refresh_portraits=lambda: redraws.append(True))
    app.root = SimpleNamespace(after=lambda delay, callback: 'pending', after_cancel=cancelled.append)
    app.events = queue.Queue()
    app._portrait_generation = 0
    app._portrait_loading_key = app._portrait_loaded_key = app._portrait_timer = None
    app._append_log = logs.append
    monkeypatch.setattr(gui, 'default_asset_cache', lambda: tmp_path/'default-cache')
    monkeypatch.setattr(gui.threading, 'Thread', lambda target, daemon: SimpleNamespace(start=lambda: workers.append(target)))
    def load(*key):
        requests.append(key)
        return (1, bytes([100, 50, 25, 255]))
    monkeypatch.setattr(gui_portraits, 'load_nurse_portrait', load)
    try:
        app._request_nurse_portrait()
        values['game_install'] = 'game-two'
        app._portrait_settings_changed()
        app._request_nurse_portrait()
        assert cancelled == ['pending']
        workers[0]()
        app._finish_nurse_portrait(app.events.get_nowait()[1])
        assert characters._INSTALLED_PORTRAITS.get(87) is None
        workers[1]()
        app._finish_nurse_portrait(app.events.get_nowait()[1])
        assert characters._INSTALLED_PORTRAITS[87] == (1, bytes([100, 50, 25, 255]))
        assert requests == [('game-one', 'cli', str(tmp_path/'default-cache')),
                            ('game-two', 'cli', str(tmp_path/'default-cache'))]
        assert len(redraws) == 2 and not logs
    finally:
        characters.set_installed_portrait(87, None)
