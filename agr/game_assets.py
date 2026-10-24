"""Read installed Source 2 resources without running or changing the game.

VPK v1/v2 layout follows Valve's VPK format and ValveResourceFormat/ValvePak.
Only requested resource payloads are read.  Indexing mounts reads their directory
trees, not all numbered chunks.  Cache bytes are checked against the archive CRC
and SHA-256 on every resolve; a filename is never treated as a valid cache hit.
RERL supplies static compiled dependencies, not runtime-created asset references.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tempfile
from typing import Iterable
import zlib


class GameAssetError(ValueError):
    """A resource, installation, or cache could not be validated."""


class MissingResourceError(GameAssetError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def safe_resource_path(value: str) -> str:
    """Canonical game paths, portable on Windows and POSIX; never host paths."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise GameAssetError(f"Invalid resource path: {value!r}")
    path = value.replace("\\", "/")
    if path.startswith("/") or ":" in path:
        raise GameAssetError(f"Absolute resource path is not allowed: {value!r}")
    parts = path.split("/")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}
    for part in parts:
        if (part in ("", ".", "..") or part[-1:] in (" ", ".")
                or any(ord(c) < 32 or c in '<>"|?*' for c in part)
                or part.split(".", 1)[0].lower() in reserved):
            raise GameAssetError(f"Unsafe resource path: {value!r}")
    return "/".join(parts).lower()


def _compiled_candidate(path: str) -> str:
    suffix = PurePosixPath(path).suffix
    return path + "_c" if suffix.startswith(".v") and not suffix.endswith("_c") else path


def normalize_install_path(path: str | Path) -> Path:
    """Accept Deadlock/, game/, game/citadel/, or its pak01_dir.vpk."""
    selected = Path(path).expanduser().resolve()
    if selected.is_file():
        selected = selected.parent
    for candidate in (selected, selected.parent, selected.parent.parent):
        if (candidate / "game/citadel/pak01_dir.vpk").is_file():
            return candidate
    raise GameAssetError(f"No game/citadel/pak01_dir.vpk found under {path}")


def _keyvalues(text: str) -> list:
    """Small ordered KeyValues reader retaining duplicate SearchPaths keys."""
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|//[^\n]*|[{}]|[^\s{}"]+', text)
    tokens = [token for token in tokens if not token.startswith("//")]
    pos = 0

    def atom(token):
        if token.startswith('"'):
            return token[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return token

    def block(nested=False):
        nonlocal pos
        result = []
        while pos < len(tokens):
            token = tokens[pos]
            pos += 1
            if token == "}":
                if not nested:
                    raise GameAssetError("Unexpected closing brace in KeyValues")
                return result
            if token == "{" or pos == len(tokens):
                raise GameAssetError("Malformed KeyValues pair")
            key = atom(token)
            value = tokens[pos]
            pos += 1
            result.append((key, block(True) if value == "{" else atom(value)))
            # Valve's conditional annotations apply to the preceding pair.
            # Keep discovery broad; a candidate still needs an actual VPK.
            if pos < len(tokens) and tokens[pos].startswith("["):
                pos += 1
        if nested:
            raise GameAssetError("Unclosed KeyValues block")
        return result

    return block()


def _pairs_named(pairs, name):
    for key, value in pairs:
        if key.casefold() == name.casefold():
            yield value
        if isinstance(value, list):
            yield from _pairs_named(value, name)


def discover_installations() -> list[Path]:
    """Discover Steam libraries, including explicit paths on other drives.

    No drive scan is performed. STEAM_PATH/STEAM_DIR provide a useful portable
    override; DEADLOCK_INSTALL can name the game directly. Ambiguity is retained
    for the caller to present, not silently resolved to an arbitrary install.
    """
    candidates = []
    direct = os.environ.get("DEADLOCK_INSTALL")
    if direct:
        candidates.append(Path(direct))
    roots = [Path(v) for key in ("STEAM_PATH", "STEAM_DIR") if (v := os.environ.get(key))]
    if os.name == "nt":
        try:
            import winreg
            for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                     (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        for key in ("ProgramFiles(x86)", "ProgramFiles"):
            if value := os.environ.get(key):
                roots.append(Path(value) / "Steam")
    else:
        roots.extend([Path.home() / ".steam/steam", Path.home() / ".local/share/Steam",
                      Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
                      Path.home() / "Library/Application Support/Steam"])
    libraries = list(roots)
    for root in roots:
        for filename in (root / "steamapps/libraryfolders.vdf", root / "config/libraryfolders.vdf"):
            if not filename.is_file():
                continue
            try:
                pairs = _keyvalues(filename.read_text(encoding="utf-8-sig"))
                libraries.extend(Path(p) for p in _pairs_named(pairs, "path") if isinstance(p, str))
                # Pre-2021 Steam uses numeric keys directly mapped to paths.
                for block in _pairs_named(pairs, "libraryfolders"):
                    if isinstance(block, list):
                        libraries.extend(Path(v) for k, v in block if k.isdigit() and isinstance(v, str))
            except (OSError, UnicodeError, GameAssetError):
                continue
    for library in dict.fromkeys(libraries):
        apps = library / "steamapps"
        candidates.append(apps / "common/Deadlock")
        manifest = apps / "appmanifest_1422450.acf"
        if manifest.is_file():
            try:
                values = _pairs_named(_keyvalues(manifest.read_text(encoding="utf-8-sig")), "installdir")
                for value in values:
                    if isinstance(value, str) and "/" not in value and "\\" not in value and value not in (".", ".."):
                        candidates.append(apps / "common" / value)
            except (OSError, UnicodeError, GameAssetError):
                pass
    found = set()
    for candidate in candidates:
        try:
            found.add(normalize_install_path(candidate))
        except GameAssetError:
            pass
    return sorted(found, key=lambda p: str(p).casefold())


@dataclass(frozen=True)
class VPKEntry:
    resource_path: str
    archive: Path
    crc32: int
    preload: bytes
    archive_index: int
    offset: int
    length: int


class VPKArchive:
    """Index standard VPK 1/2; unsupported variants fail explicitly."""
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        before = self.path.stat()
        self.signature = (before.st_size, before.st_mtime_ns)
        with self.path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12:
                raise GameAssetError(f"Truncated VPK header: {self.path}")
            signature, self.version, tree_size = struct.unpack("<III", header)
            if signature != 0x55AA1234 or self.version not in (1, 2):
                raise GameAssetError(f"Unsupported VPK signature/version in {self.path}: {self.version}")
            self.header_size = 12 if self.version == 1 else 28
            sections = stream.read(16) if self.version == 2 else b""
            if len(sections) != self.header_size - 12:
                raise GameAssetError(f"Truncated VPK v2 header: {self.path}")
            if tree_size > 256 * 1024 * 1024 or tree_size > before.st_size - self.header_size:
                raise GameAssetError(f"Invalid VPK tree size: {self.path}")
            tree = stream.read(tree_size)
            self.data_offset = self.header_size + tree_size
            self.data_size = (struct.unpack("<IIII", sections)[0] if sections
                              else before.st_size - self.data_offset)
            if self.data_offset + self.data_size > before.st_size:
                raise GameAssetError(f"VPK embedded data section exceeds file: {self.path}")
        self.directory_sha256 = _digest(header + sections + tree)
        self.entries = {}
        pos = 0

        def string():
            nonlocal pos
            end = tree.find(b"\0", pos)
            if end < 0:
                raise GameAssetError(f"Unterminated VPK tree string: {self.path}")
            try:
                result = tree[pos:end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GameAssetError(f"Invalid VPK resource name: {self.path}") from exc
            pos = end + 1
            return result

        while extension := string():
            while directory := string():
                while name := string():
                    if pos + 18 > len(tree):
                        raise GameAssetError(f"Truncated VPK entry: {self.path}")
                    crc, count, archive_index, offset, length, terminator = struct.unpack_from("<IHHIIH", tree, pos)
                    pos += 18
                    if terminator != 0xFFFF or pos + count > len(tree):
                        raise GameAssetError(f"Invalid VPK entry/preload: {self.path}")
                    preload = tree[pos:pos + count]
                    pos += count
                    filename = name + ("." + extension if extension != " " else "")
                    resource = safe_resource_path((directory + "/" if directory != " " else "") + filename)
                    if resource in self.entries:
                        raise GameAssetError(f"Duplicate case-insensitive resource in {self.path}: {resource}")
                    # EntryOffset is unused when all bytes are in the directory
                    # preload (or the file is empty). ValvePak likewise seeks a
                    # payload only when EntryLength > 0. Preserve the bounds
                    # check for real embedded payloads and CRC-check every read.
                    if length and archive_index == 0x7FFF and offset + length > self.data_size:
                        raise GameAssetError(
                            f"VPK embedded entry outside data: {resource} "
                            f"[archive={self.path}; version={self.version}; "
                            f"archive_index=0x{archive_index:04X}; preload_bytes={count}; "
                            f"offset={offset}; payload_length={length}; data_size={self.data_size}]")
                    self.entries[resource] = VPKEntry(resource, self.path, crc, preload, archive_index, offset, length)
        if any(tree[pos:]):
            raise GameAssetError(f"Unexpected nonzero VPK tree trailing data: {self.path}")
        self.assert_unchanged()

    def assert_unchanged(self):
        st = self.path.stat()
        if (st.st_size, st.st_mtime_ns) != self.signature:
            raise GameAssetError(f"Game archive changed during this job; restart after the update: {self.path}")

    def read(self, entry: VPKEntry) -> bytes:
        self.assert_unchanged()
        payload = b""
        if entry.length:
            if entry.archive_index == 0x7FFF:
                path, offset = self.path, self.data_offset + entry.offset
            else:
                if not self.path.name.endswith("_dir.vpk"):
                    raise GameAssetError(f"External VPK chunks require a _dir.vpk archive: {self.path}")
                path = self.path.with_name(self.path.name[:-8] + f"_{entry.archive_index:03d}.vpk")
                offset = entry.offset
            if not path.is_file():
                raise MissingResourceError(f"Missing numbered VPK chunk for {entry.resource_path}: {path}")
            before = path.stat()
            if offset + entry.length > before.st_size:
                raise GameAssetError(f"VPK payload outside archive: {entry.resource_path}")
            with path.open("rb") as stream:
                stream.seek(offset)
                payload = stream.read(entry.length)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise GameAssetError(f"Game archive changed while reading {entry.resource_path}")
        data = entry.preload + payload
        if zlib.crc32(data) & 0xFFFFFFFF != entry.crc32:
            raise GameAssetError(f"CRC32 mismatch in {entry.archive}: {entry.resource_path}")
        self.assert_unchanged()
        return data


def resource_references(data: bytes) -> tuple[str, ...]:
    """Read Source 2 v12 resource RERL; reject truncated/unknown layouts."""
    if len(data) < 16:
        raise GameAssetError("Truncated compiled Source 2 resource")
    _, version, _, relative, count = struct.unpack_from("<IHHII", data)
    if version != 12:
        raise GameAssetError(f"Unsupported compiled resource header version: {version}")
    if count > (len(data) - 16) // 12:
        raise GameAssetError("Compiled resource block table count exceeds file")
    result = []
    for i in range(count):
        entry = 8 + relative + 12 * i
        if entry < 16 or entry + 12 > len(data):
            raise GameAssetError("Truncated compiled resource block table")
        kind, offset, size = struct.unpack_from("<4sII", data, entry)
        start, end = entry + 4 + offset, entry + 4 + offset + size
        if not 0 <= start <= end <= len(data):
            raise GameAssetError("Compiled resource block outside file")
        if kind != b"RERL":
            continue
        if size < 8:
            raise GameAssetError("Truncated RERL header")
        offset, number = struct.unpack_from("<II", data, start)
        if number > size // 16:
            raise GameAssetError("RERL entry count exceeds block")
        for j in range(number):
            ref = start + offset + j * 16
            if not start + 8 <= ref <= end - 16:
                raise GameAssetError("RERL reference outside block")
            _, name_offset = struct.unpack_from("<Qq", data, ref)
            pos = ref + 8 + name_offset
            if not start <= pos < end:
                raise GameAssetError("RERL name outside block")
            terminator = data.find(b"\0", pos, end)
            if terminator < 0:
                raise GameAssetError("Unterminated RERL name")
            try:
                result.append(safe_resource_path(data[pos:terminator].decode("utf-8")))
            except UnicodeDecodeError as exc:
                raise GameAssetError("Invalid RERL name encoding") from exc
    return tuple(dict.fromkeys(result))


def _write_verified(path: Path, data: bytes, digest: str):
    # Never follow a cache symlink into another folder.
    if path.is_symlink():
        raise GameAssetError(f"Cache entry is a symlink: {path}")
    if path.is_file() and _digest(path.read_bytes()) == digest:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".agr-", delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        if _digest(temp.read_bytes()) != digest:
            raise GameAssetError(f"Cache write verification failed: {path}")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass(frozen=True)
class ResolvedResource:
    resource_path: str
    local_path: Path
    sha256: str
    archive: Path
    dependencies: tuple[str, ...]
    type: str

    def as_dict(self):
        return {"resource_path": self.resource_path, "local_path": str(self.local_path),
                "sha256": self.sha256, "archive": str(self.archive),
                "dependencies": list(self.dependencies), "type": self.type}


class GameAssetProvider:
    """Mount a validated installation and materialize immutable working sets."""
    def __init__(self, install_path, cache_dir, map_name=None):
        self.install_path = normalize_install_path(install_path)
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        game = self.install_path / "game"
        if self.cache_dir == self.install_path or self.install_path in self.cache_dir.parents:
            raise GameAssetError("AGR cache must be outside the installed game folder")
        self.warnings = []
        archives = []
        # Selected map resources override packaged game resources. A replay map
        # label such as 'start' is not reinterpreted as dl_midtown.
        self.map_name = map_name
        if map_name:
            map_path = safe_resource_path(str(map_name))
            if not map_path.startswith("maps/"):
                map_path = "maps/" + map_path
            if not map_path.endswith(".vpk"):
                map_path += ".vpk"
            selected_map = game / "citadel" / map_path
            if not selected_map.is_file():
                raise MissingResourceError(f"Selected map archive is not installed: {selected_map}")
            archives.append(selected_map)
        search_paths = []
        ignored_search_paths = []
        stock_roots = {(game / name).resolve() for name in ('citadel', 'core')}
        gameinfo = game / "citadel/gameinfo.gi"
        if gameinfo.is_file():
            for block in _pairs_named(_keyvalues(gameinfo.read_text(encoding="utf-8-sig")), "SearchPaths"):
                if not isinstance(block, list):
                    continue
                for key, value in block:
                    if not isinstance(value, str) or key.lower() not in {"game", "mod"}:
                        continue
                    value = value.replace("\\", "/")
                    if value.startswith("|gameinfo_path|"):
                        directory = gameinfo.parent / value[len("|gameinfo_path|"):]
                    elif value.startswith("|all_source_engine_paths|"):
                        directory = game / value[len("|all_source_engine_paths|"):]
                    elif "|" in value or "*" in value:
                        self.warnings.append(f"Unsupported game search path retained as warning: {value}")
                        continue
                    else:
                        directory = game / value
                    directory = directory.resolve()
                    if directory != game and game not in directory.parents:
                        self.warnings.append(f"External game search path is not mounted: {value}")
                        continue
                    # Installed exports use the base Citadel/Core archive layout.
                    # A user gameinfo can place addon or renamed mod folders ahead
                    # of these roots; following it would replace recorded models
                    # with unrelated payloads despite valid archive CRCs.
                    stock_archive = (directory.suffix.lower() == '.vpk'
                                     and directory.name.lower().endswith('_dir.vpk')
                                     and directory.parent in stock_roots)
                    if directory not in stock_roots and not stock_archive:
                        ignored_search_paths.append({'path': value, 'reason': 'custom_search_path'})
                        self.warnings.append(f"Custom game search path is not mounted for stock exports: {value}")
                        continue
                    search_paths.append(directory)
        # Stock fallbacks are explicit in metadata. Missing optional Core is
        # reported when a requested dependency cannot be resolved.
        search_paths.extend((game / "citadel", game / "core"))
        for directory in dict.fromkeys(search_paths):
            if directory.suffix.lower() == ".vpk":
                if directory.is_file():
                    archives.append(directory)
                continue
            if not directory.is_dir():
                continue
            primary = directory / "pak01_dir.vpk"
            if primary.is_file():
                archives.append(primary)
            # Additional explicitly mounted stock package directories, not
            # addon folders or entire drive scans.
            archives.extend(sorted(p for p in directory.glob("*_dir.vpk") if p != primary))
        self.archives = [VPKArchive(p) for p in dict.fromkeys(archives)]
        if not self.archives:
            raise GameAssetError("No supported game VPK archives were mounted")
        self._archives_by_path = {archive.path: archive for archive in self.archives}
        self.resources = {}
        self.shadowed_resources = 0
        for archive in self.archives:
            for resource, entry in archive.entries.items():
                if resource in self.resources:
                    self.shadowed_resources += 1
                else:
                    self.resources[resource] = entry
        self.snapshot = {"schema_version": 1, "install_path": str(self.install_path),
                         "map_name": map_name, "mount_precedence": "first mount wins",
                         "mount_policy": "base_game_archives_and_selected_map_v1",
                         "ignored_search_paths": ignored_search_paths,
                         "archives": [{"path": str(a.path), "version": a.version,
                                       "size": a.signature[0], "mtime_ns": a.signature[1],
                                       "directory_sha256": a.directory_sha256,
                                       "resource_count": len(a.entries)} for a in self.archives],
                         "resource_count": len(self.resources), "shadowed_resources": self.shadowed_resources,
                         "warnings": self.warnings, "metadata": {}}
        for filename, key in ((game / "citadel/steam.inf", "steam_inf"),
                              (self.install_path.parent.parent / "appmanifest_1422450.acf", "steam_manifest"),
                              (gameinfo, "gameinfo")):
            if filename.is_file():
                data = filename.read_bytes()
                self.snapshot["metadata"][key] = {"path": str(filename), "sha256": _digest(data),
                                                   "text": data.decode("utf-8-sig", errors="replace")}
        self.snapshot["note"] = ("Steam build/client metadata is not the demo build. Directory hashes identify mount trees; "
                                 "requested payloads receive independent CRC32 and SHA-256 validation. "
                                 "Only the base Citadel/Core archive layout and the explicitly selected map are mounted. "
                                 "Loose files, custom search paths and addon folders are excluded; archive paths do not prove unmodified game content.")
        self.snapshot["snapshot_id"] = _digest(_json_bytes(self.snapshot))

    def lookup(self, resource_path: str) -> VPKEntry | None:
        path = safe_resource_path(resource_path)
        return self.resources.get(path) or self.resources.get(_compiled_candidate(path))

    def resolve(self, resource_path: str) -> ResolvedResource:
        entry = self.lookup(resource_path)
        if entry is None:
            raise MissingResourceError(f"Resource is not present in mounted game archives: {resource_path}")
        data = self._archives_by_path[entry.archive].read(entry)
        digest = _digest(data)
        dependencies = resource_references(data) if entry.resource_path.endswith("_c") else ()
        path = self.cache_dir / "objects" / digest[:2] / digest / PurePosixPath(entry.resource_path).name
        if not path.resolve().is_relative_to(self.cache_dir):
            raise GameAssetError(f"Cache path escapes its root: {path}")
        _write_verified(path, data, digest)
        return ResolvedResource(entry.resource_path, path, digest, entry.archive, dependencies,
                                PurePosixPath(entry.resource_path).suffix)

    def resource_identity(self, resource_path: str) -> dict:
        """Hash a CRC-verified mounted payload without materializing another copy."""
        entry = self.lookup(resource_path)
        if entry is None:
            raise MissingResourceError(f'Resource is not present in mounted game archives: {resource_path}')
        data = self._archives_by_path[entry.archive].read(entry)
        return {'resource_path': entry.resource_path, 'archive': str(entry.archive), 'sha256': _digest(data)}

    def materialize_closure(self, paths: Iterable[str]) -> dict:
        requested = list(dict.fromkeys(safe_resource_path(p) for p in paths))
        pending = deque((p, None) for p in requested)
        found = {}
        missing = {}
        errors = {}
        parents = {}
        while pending:
            path, parent = pending.popleft()
            entry = self.lookup(path)
            canonical = entry.resource_path if entry else _compiled_candidate(path)
            parents.setdefault(canonical, [])
            if parent and parent not in parents[canonical]:
                parents[canonical].append(parent)
            if canonical in found or canonical in missing or canonical in errors:
                continue
            if len(found) + len(missing) + len(errors) >= 100000:
                raise GameAssetError("Requested dependency closure exceeds 100,000 resources")
            try:
                result = self.resolve(path)
            except MissingResourceError as exc:
                missing[canonical] = str(exc)
                continue
            except GameAssetError as exc:
                errors[canonical] = str(exc)
                continue
            found[canonical] = result
            pending.extend((dependency, canonical) for dependency in result.dependencies)
        identities = {p: r.sha256 for p, r in sorted(found.items())}
        closure_id = _digest(_json_bytes({"resources": identities, "requested": requested,
                                          "snapshot_id": self.snapshot["snapshot_id"],
                                          "missing": sorted(missing), "errors": errors}))
        root = self.cache_dir / "working_sets" / closure_id / "resources"
        for path, result in found.items():
            destination = root.joinpath(*path.split("/"))
            if not destination.resolve().is_relative_to(self.cache_dir):
                raise GameAssetError(f"Working-set path escapes cache root: {destination}")
            # Copy rather than hard-link: converter writes cannot poison the
            # content-addressed object cache.
            _write_verified(destination, result.local_path.read_bytes(), result.sha256)
        root.mkdir(parents=True, exist_ok=True)
        report = {"schema_version": 1, "status": "complete" if not missing and not errors else "incomplete",
                  "closure_id": closure_id, "root": str(root), "requested": requested,
                  "resources": {p: {**r.as_dict(), "local_path": str(root.joinpath(*p.split("/"))),
                                     "cache_path": str(r.local_path)} for p, r in sorted(found.items())},
                  "missing": [{"resource_path": p, "reason": reason, "referenced_by": parents[p]}
                              for p, reason in sorted(missing.items())],
                  "errors": [{"resource_path": p, "reason": reason, "referenced_by": parents[p]}
                             for p, reason in sorted(errors.items())],
                  "snapshot": self.snapshot,
                  "coverage_note": "RERL static dependency closure only; runtime asset references and behavior remain separate."}
        report_path = root.parent / "manifest.json"
        encoded = _json_bytes(report)
        _write_verified(report_path, encoded, _digest(encoded))
        report["manifest_path"] = str(report_path)
        return report
