"""Measure synthetic moving-camera files with the real writer, not video assets."""
import argparse
import copy
import json
import math
from pathlib import Path

from agr.shot_package import write_shot_collection, read_shot_collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    template = json.loads((Path(__file__).parents[1] / "docs/interchange/minimal.crav").read_text())
    results = []
    for seconds, fps in ((10, 30), (60, 60)):
        data = copy.deepcopy(template)
        shot = data["shots"][0]
        camera = shot["camera"]
        base = camera["samples"][0]
        samples = []
        for frame in range(seconds * fps):
            sample = copy.deepcopy(base)
            t = frame / fps
            angle = t * .1
            sample.update(frame=frame, video_time=t, replay_time=t,
                          position=[1000 * math.cos(angle), 1000 * math.sin(angle), 256 + t],
                          forward=[math.cos(angle), math.sin(angle), 0],
                          right=[math.sin(angle), -math.cos(angle), 0])
            samples.append(sample)
        camera.update(fps=fps, frame_count=len(samples), samples=samples)
        end = 64 + samples[-1]["replay_time"] * 64
        shot["range"].update(end_tick=math.floor(end + 1e-6), end_tick_exact=end)
        data["replay"]["total_ticks"] = math.ceil(end) + 2
        output = write_shot_collection(data, args.out / f"synthetic-{seconds}s-{fps}fps.crav")
        assert read_shot_collection(output, resolve_paths=False) == data
        results.append(dict(seconds=seconds, fps=fps, samples=len(samples), bytes=output.stat().st_size))
    args.out.mkdir(parents=True, exist_ok=True)
    report = dict(kind="synthetic; not a game capture or visual proof", measurements=results)
    (args.out / "sizes.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
