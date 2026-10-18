"""Windows CRLF patch transport must not break LF upstream dependency source."""
import subprocess

import pytest

from tools.codeql_build_helpers import apply_patch


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
