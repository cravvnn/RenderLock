"""Windows CRLF patch transport must not break LF upstream dependency source."""
import subprocess

import pytest

from tools.codeql_build_helpers import apply_patch
from tools import codeql_build_helpers


def test_crlf_patch_applies_to_lf_source_without_relaxing_context(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / ".gitattributes").write_bytes(b"*.cs text eol=lf\n")
    (repo / "source.cs").write_bytes(b"old\n")
    patch = tmp_path / "change.patch"
    patch.write_bytes(b"--- a/source.cs\r\n+++ b/source.cs\r\n@@ -1 +1 @@\r\n-old\r\n+new\r\n")
    apply_patch(repo, patch, tmp_path / "normalized")
    assert (repo / "source.cs").read_bytes() == b"new\n"
    # A changed upstream file must still refuse the same patch.
    with pytest.raises(subprocess.CalledProcessError):
        apply_patch(repo, patch, tmp_path / "normalized-again")
    assert (repo / "source.cs").read_bytes() == b"new\n"


def test_geometry_preparation_cannot_patch_the_material_dependency(tmp_path, monkeypatch):
    patches = []
    def checkout(path, revision):
        path.mkdir(parents=True)
        # Satisfy the existing native lighting dependency readiness guard.
        native = path / 'ValveResourceFormat/bin/Debug/runtimes/win-x64/native/libSkiaSharp.dll'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'fixture only')
        return path / 'ValveResourceFormat/ValveResourceFormat.csproj'
    monkeypatch.setattr(codeql_build_helpers, 'checkout', checkout)
    monkeypatch.setattr(codeql_build_helpers, 'apply_patch',
                        lambda path, source, workspace: patches.append((path.name, source.name)))
    monkeypatch.setattr(codeql_build_helpers, 'run', lambda *args, **kwargs: None)
    codeql_build_helpers.prepare(tmp_path / 'work', 'fixture-dotnet')
    assert [name for target, name in patches if target == 'geometry'] == [
        'vrf-skeletonless-morph-animation.patch']
    assert ('particle', 'vrf-skeletonless-morph-animation.patch') not in patches
    properties = codeql_build_helpers.properties(tmp_path / 'work')
    assert '-p:ParticleVrfProject=' + str(tmp_path / 'work/particle/ValveResourceFormat/ValveResourceFormat.csproj') in properties
    assert '-p:ParticleGeometryVrfProject=' + str(tmp_path / 'work/geometry/ValveResourceFormat/ValveResourceFormat.csproj') in properties
