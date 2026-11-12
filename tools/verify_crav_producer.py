"""Cross-repository synthetic conformance. Does not imply live camera capture."""
import argparse
import ast
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agr import shot_package
from agr.blender_shot_package import camera_transform, scene_frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dolly-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.dolly_root))
    from dolly import crav_contract
    from dolly.camera_export import Frame, Take
    from dolly.crav_export import build_crav, export_crav
    from dolly.path import Project

    # The vendored validator must match source semantics, including constants;
    # whitespace/UTF-8 BOM differences are immaterial.
    reference = Path(shot_package.__file__).read_text(encoding="utf-8-sig").split("def _strict_json(", 1)[0]
    vendor = Path(crav_contract.__file__).read_text(encoding="utf-8-sig")
    assert ast.dump(ast.parse(reference)) == ast.dump(ast.parse(vendor)), "Validator copies drifted"
    replay = dict(path="synthetic.dem", sha256="a"*64, build=6851,
                  map="dl_midtown", tick_rate=64, total_ticks=10000)
    frames = [Frame(i, i/30, (100 + i, 200, 300), (0, 90, 0), 90, i/30) for i in range(60)]
    take = Take(frames, 60, [], source="native-view-hook", size=(1920, 1080), fixed_step=True)
    document = build_crav(Project(name="Synthetic cross-repo shot", start_tick=640), take,
                          fps=30, replay_before=replay, replay_after=replay)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "producer.crav"
    export_crav(document, path)
    loaded = shot_package.read_shot_collection(path, resolve_paths=False)
    assert loaded == document
    shot = loaded["shots"][0]
    for i, sample in enumerate(shot["camera"]["samples"]):
        position = camera_transform(sample, {})["location"]
        assert max(abs(a-b) for a,b in zip(position, [(100+i)*.0254, 5.08, 7.62])) < 1e-10
        assert abs(scene_frame(shot, sample, scene_start_tick=640, tick_rate=64, fps=30)-(i+1)) < 1e-10
    for case in ("identity", "timing", "media", "scene", "state", "profile"):
        bad = copy.deepcopy(document)
        if case == "identity": del bad["replay"]["sha256"]
        elif case == "timing": bad["shots"][0]["camera"]["samples"][1]["replay_time"] = 0
        elif case == "media": bad["shots"][0]["media"] = {}
        elif case == "scene": bad["scene"] = {"path": "scene.rlscene"}
        elif case == "state": bad["shots"][0]["camera"]["samples"][0]["state"] = "projection-only"
        elif case == "profile": bad["profile"] = "future"
        for validator in (crav_contract.validate_shot_collection, shot_package.validate_shot_collection):
            try:
                validator(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"Accepted invalid {case}")
    report = dict(status="PASS", scope="synthetic producer/consumer; no live capture or Blender render",
                  validator_ast_equal=True, frames=60, corruption_cases=6,
                  bytes=path.stat().st_size)
    (args.out / "RESULT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
