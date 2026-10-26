"""Run two frozen local probes with identical input; no SDK, HTTP, or live cache writes."""
import argparse
import ctypes
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import time
import zlib


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def peak_rss(process):
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak", "working", "paged_peak", "paged", "nonpaged_peak", "nonpaged", "pagefile", "pagefile_peak")
            ]
        info = Counters()
        info.cb = ctypes.sizeof(info)
        query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        query.restype = wintypes.BOOL
        return info.peak if query(int(process._handle), ctypes.byref(info), info.cb) else None
    try:
        for line in Path(f"/proc/{process.pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def run(executable, request, folder, timeout):
    folder.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    with (folder / "stderr.log").open("wb") as errors:
        process = subprocess.Popen([str(executable), str(request), str(folder)], stdout=subprocess.DEVNULL,
            stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        peak = None
        while True:
            if time.perf_counter() - started > timeout:
                process.kill()
                process.wait()
                raise TimeoutError(f"probe exceeded {timeout}s: {executable}")
            observed = peak_rss(process)
            if observed is not None:
                peak = max(peak or 0, observed)
            if process.poll() is not None:
                break
            time.sleep(0.005)
    if process.returncode:
        error = (folder / "stderr.log").read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(f"probe failed ({process.returncode}): {executable}\n{error[-4000:]}")
    row = json.loads((folder / "sample.json").read_text(encoding="utf-8-sig"))
    row["process_wall_ms"] = (time.perf_counter() - started) * 1000
    row["peak_rss_bytes"] = peak
    sizes, hashes = {}, {}
    for name in ["indexed.json", "indexed.wire.json", "view.json", "view.wire.json", "snapshot.wire.json", "memory.json", "n-records.json"]:
        path = folder / name
        if not path.exists() and name == "n-records.json":
            sizes[name] = {"json_bytes": 0, "gzip_bytes": 0, "zlib_fast_bytes": 0}
            hashes[name] = sha(b"")
            continue
        raw = path.read_bytes()
        sizes[name] = {"json_bytes": len(raw), "gzip_bytes": len(gzip.compress(raw, compresslevel=6, mtime=0)), "zlib_fast_bytes": len(zlib.compress(raw, level=1))}
        if name == "memory.json" and json.loads(raw) is None:
            sizes[name] = {key: 0 for key in sizes[name]}
        hashes[name] = sha(raw)
    database = folder / "cache" / "rust-artifacts-v1.sqlite"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row["cache"] = {"entries": db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
            "payload_bytes": db.execute("SELECT COALESCE(SUM(length(payload)),0) FROM artifacts").fetchone()[0],
            "database_bytes": database.stat().st_size}
    row["sizes"], row["hashes"] = sizes, hashes
    (folder / "observations.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def distribution(values):
    values = [v for v in values if isinstance(v, (float, int)) and math.isfinite(v)]
    return {"median": statistics.median(values), "min": min(values), "max": max(values)} if values else None


def delta(before, after):
    return {"before": before, "after": after, "difference": after - before,
        "change_percent": (after / before - 1) * 100 if before else None,
        "before_over_after": before / after if after else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before-exe", "after-exe", "input", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--summarize-only", action="store_true", help="reuse completed samples without running probes")
    args = parser.parse_args()
    if args.samples < 1 or args.warmups < 0 or args.timeout_seconds <= 0:
        parser.error("samples must be positive; warmups must be nonnegative")
    args.output.mkdir(parents=True, exist_ok=True)
    rows = {"before": [], "after": []}
    executables = {"before": args.before_exe.resolve(), "after": args.after_exe.resolve()}
    request = args.input.resolve()
    if args.summarize_only:
        previous = json.loads((args.output / "comparison.json").read_text(encoding="utf-8"))
        if any(previous["binaries"][name]["sha256"] != sha(path.read_bytes()) for name,path in executables.items()):
            raise RuntimeError("binary changed; cannot reuse previous samples")
        for name in executables:
            for i in range(args.samples):
                folder = args.output / name / f"sample-{i}"
                row = json.loads((folder / "observations.json").read_text(encoding="utf-8"))
                if json.loads((folder / "memory.json").read_bytes()) is None:
                    row["sizes"]["memory.json"] = {key: 0 for key in row["sizes"]["memory.json"]}
                rows[name].append(row)
    else:
        for name, executable in executables.items():
            for i in range(args.warmups):
                run(executable, request, args.output / name / f"warmup-{i}", args.timeout_seconds)
        for i in range(args.samples):
            for name in (("before", "after") if i % 2 == 0 else ("after", "before")):
                row = run(executables[name], request, args.output / name / f"sample-{i}", args.timeout_seconds)
                rows[name].append(row)
                print(f"{name} sample {i + 1}: analysis={row['analysis_ms']:.3f}ms", flush=True)
    all_rows = rows["before"] + rows["after"]
    if any(row["input_sha256"] != sha(request.read_bytes()) for row in all_rows):
        raise RuntimeError("input bytes differ between probes")
    if len({row["config_sha256"] for row in all_rows}) != 1:
        raise RuntimeError("effective configurations differ")
    for name, samples in rows.items():
        for key in ("structure_sha256", "qualification_sha256"):
            if len({row[key] for row in samples}) != 1:
                raise RuntimeError(f"{name} results are nondeterministic: {key}")
        for artifact in samples[0]["hashes"]:
            if len({row["hashes"][artifact] for row in samples}) != 1:
                raise RuntimeError(f"{name} output bytes are nondeterministic: {artifact}")
    first = {name: samples[0] for name, samples in rows.items()}
    fields = [key for key, value in all_rows[0].items() if key.endswith("_ms") and isinstance(value, (float, int)) or key == "peak_rss_bytes"]
    times = {field: {name: distribution([row.get(field) for row in samples]) for name, samples in rows.items()} for field in fields}
    for field, values in times.items():
        if values["before"] and values["after"]:
            values["delta"] = delta(values["before"]["median"], values["after"]["median"])
    phases = {key: {name: distribution([row["phases_ms"].get(key) for row in samples]) for name,samples in rows.items()} for key in sorted(set().union(*(row["phases_ms"] for row in all_rows)))}
    sizes = {name: {metric: delta(first["before"]["sizes"][name][metric], first["after"]["sizes"][name][metric]) for metric in first["before"]["sizes"][name]} for name in first["before"]["sizes"]}
    # Segment annotations add columns without changing existing coordinates or clocks.
    # Only these named additive columns are excluded; all pre-existing fields still compare.
    geometry = {}
    memory = {}
    for name in executables:
        folder = args.output / name / "sample-0"
        indexed = json.loads((folder / "indexed.json").read_bytes())
        geometry[name] = [{key: value for key,value in level.items() if key not in ("trendStates", "trendStateKnownIndices")} for level in indexed["levels"]]
        memory[name] = {"levels": json.loads((folder / "memory.json").read_bytes()), "records": json.loads((folder / "n-records.json").read_bytes()) if (folder / "n-records.json").exists() else None}
    report = {"geometry_equal": geometry["before"] == geometry["after"], "n_memory_equal": memory["before"] == memory["after"], "symbol": first["before"]["symbol"], "bars": first["before"]["bars"], "input_sha256": all_rows[0]["input_sha256"],
        "config_sha256": all_rows[0]["config_sha256"], "samples_per_version": args.samples, "warmups_per_version": args.warmups,
        "binaries": {name: {"path": str(path), "sha256": sha(path.read_bytes()), "engine": first[name]["engine"], "version": first[name]["version"]} for name, path in executables.items()},
        "structure_equal": first["before"]["structure_sha256"] == first["after"]["structure_sha256"],
        "qualification_equal": first["before"]["qualification_sha256"] == first["after"]["qualification_sha256"],
        "snapshot_equal": first["before"]["hashes"]["snapshot.wire.json"] == first["after"]["hashes"]["snapshot.wire.json"],
        "timings": times, "phase_timings": phases, "sizes": sizes,
        "counts": {name: {key: row[key] for key in ("points", "qualification_counts", "first_n_counts")} for name, row in first.items()},
        "cache_payload_bytes": delta(first["before"]["cache"]["payload_bytes"], first["after"]["cache"]["payload_bytes"]),
        "cache_samples": {name: [row["cache"] for row in samples] for name, samples in rows.items()}, "samples": rows,
        "scope": "analysis excludes input parsing, projection, encoding, SQLite, SDK/Redis, HTTP, Worker pipe; absent memory field counts as 0 B (null file is a sentinel); isolated artifact payload fixes pipeline=null; peak RSS includes entire probe"}
    (args.output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# {report['symbol']} 性能对照", "", f"{report['bars']} 根同输入，预热各 {args.warmups} 次、交替采样各 {args.samples} 次。", "", "| 指标（中位数） | 旧 | 新 | 变化 |", "| --- | ---: | ---: | ---: |"]
    for field, value in times.items():
        if "delta" in value:
            d = value["delta"]
            lines.append(f"| {field} | {d['before']:.4f} | {d['after']:.4f} | {d['difference']:+.4f} |")
    lines += ["", "分阶段内部计时（嵌套指标不可重复相加）：", "", "| 阶段 | 旧中位ms | 新中位ms |", "| --- | ---: | ---: |"]
    for name,values in phases.items():
        before = f"{values['before']['median']:.4f}" if values["before"] else "—"
        after = f"{values['after']['median']:.4f}" if values["after"] else "—"
        lines.append(f"| {name} | {before} | {after} |")
    lines += ["", "| 数据（B） | 旧 | 新 | 变化 |", "| --- | ---: | ---: | ---: |"]
    for name, metrics in sizes.items():
        for metric, d in metrics.items():
            lines.append(f"| {name} {metric} | {d['before']} | {d['after']} | {d['difference']:+} |")
    d = report["cache_payload_bytes"]
    lines.append(f"| 实际SQLite结果缓存BLOB | {d['before']} | {d['after']} | {d['difference']:+} |")
    lines += ["", f"完整结构含新增列一致：{report['structure_equal']}；原有结构字段一致：{report['geometry_equal']}；N记忆一致：{report['n_memory_equal']}；资格一致：{report['qualification_equal']}；行情快照一致：{report['snapshot_equal']}。", "", report["scope"], ""]
    (args.output / "comparison.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
