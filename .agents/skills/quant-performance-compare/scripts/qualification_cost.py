"""Compare current four-level analysis with no N/qualification, N only, and qualification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

MODES = ("structure", "memory", "qualified")
FIELDS = ("analysis_ms", "init_ms", "polyline_ms", "level1_ms", "level2_ms", "level3_ms", "qualification_ms", "n_memory_ms", "qualification_and_mapping_ms", "output_ms")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def distribution(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def run(executable, request, output, mode):
    with output.with_suffix(".stderr.log").open("wb") as errors:
        subprocess.run([str(executable), str(request), str(output), mode], check=True, timeout=120,
            stdout=subprocess.DEVNULL, stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    row = json.loads(output.read_bytes())
    if row["mode"] != mode:
        raise RuntimeError("probe returned a different mode")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--samples", type=int, default=9)
    args = parser.parse_args()
    if args.warmups < 1 or args.samples < 3:
        parser.error("at least one warmup and three samples required")
    args.output.mkdir(parents=True, exist_ok=False)
    executable, request = args.exe.resolve(), args.input.resolve()
    rows = {mode: [] for mode in MODES}
    for phase, count in (("warmup", args.warmups), ("sample", args.samples)):
        for index in range(count):
            for offset in range(3):
                mode = MODES[(index + offset) % 3]
                row = run(executable, request, args.output / f"{phase}-{index}-{mode}.json", mode)
                if phase == "sample":
                    rows[mode].append(row)
                    print(f"{index + 1} {mode}: {row['analysis_ms']:.4f}ms", flush=True)
    all_rows = sum(rows.values(), [])
    if any(row["input_sha256"] != sha(request.read_bytes()) for row in all_rows):
        raise RuntimeError("input bytes differ")
    if len({row["engine"] for row in all_rows}) != 1 or len({row["structure_sha256"] for row in all_rows}) != 1:
        raise RuntimeError("engine or full four-level structure differs")
    for mode, samples in rows.items():
        for key in ("config_sha256", "n_memory_sha256", "qualification_sha256"):
            if len({row[key] for row in samples}) != 1:
                raise RuntimeError(f"nondeterministic {mode} {key}")
        if mode != "qualified" and any(any(level for level in row["intervals"]) for row in samples):
            raise RuntimeError("disabled qualification returned intervals")
    if rows["memory"][0]["n_memory_sha256"] != rows["qualified"][0]["n_memory_sha256"]:
        raise RuntimeError("qualification switch changed N memory")
    timings = {mode: {field: distribution([row[field] for row in samples]) for field in FIELDS} for mode, samples in rows.items()}
    report = {"symbol": all_rows[0]["symbol"], "bars": all_rows[0]["bars"], "version": all_rows[0]["version"], "engine": all_rows[0]["engine"],
        "input_sha256": all_rows[0]["input_sha256"], "exe_sha256": sha(executable.read_bytes()), "samples_per_mode": args.samples,
        "warmups_per_mode": args.warmups, "structure_equal": True, "memory_equal_between_memory_and_qualified": True,
        "timings": timings, "config_sha256": {mode: samples[0]["config_sha256"] for mode, samples in rows.items()},
        "enabled": {mode: samples[0]["enabled"] for mode, samples in rows.items()}, "samples": rows,
        "scope": "current same exe; isolated process per sample, rotating three modes; includes init, four levels and six-state observations, optional N/qualification and indexed output; excludes input parsing, projection, encoding, cache, SDK/Redis, Worker, HTTP and account"}
    (args.output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({mode: values["analysis_ms"] for mode, values in timings.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
