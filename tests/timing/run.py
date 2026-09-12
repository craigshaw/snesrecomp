#!/usr/bin/env python3
"""Compile and compare real generated C with the real interpreter bridge.

Exit status: 0 for timing parity, 1 for a timing mismatch, 2 for a
broken harness/tool, interpreter-budget mismatch or architectural mismatch.
All programs are synthetic; no game ROM or private profile is used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "recompiler"))
from v2.emit_function import emit_function

# Expected interpreter totals are hand-derived bus/internal-cycle budgets.
# RTL has four bus reads (opcode and three stack bytes) and two internal
# cycles: 4*8 + 2*6 = 44 in SlowROM with the stack in low WRAM.
CASES = [
    dict(name="nop_rtl", code=[0xEA, 0x6B], cpu=8, master=58),
    dict(name="lda8_rtl", code=[0xA9, 0x5A, 0x6B], cpu=8, master=60),
    dict(name="lda16_rtl", code=[0xA9, 0x34, 0x12, 0x6B], m=0,
         cpu=9, master=68),
    dict(name="wram_store", code=[0xA9, 0x5A, 0x8D, 0x00, 0x10, 0x6B],
         cpu=12, master=92, write=16),
    dict(name="mmio_store", code=[0xA9, 0x00, 0x8D, 0x00, 0x42, 0x6B],
         cpu=12, master=90, write=16),
    dict(name="internal_before_mmio", code=[0xEA, 0xA9, 0x00, 0x8D, 0x00, 0x42, 0x6B],
         cpu=14, master=104, write=30),
    # FastROM changes instruction fetches to six clocks, but low-WRAM stack
    # and data accesses still cost eight. This can reverse the timing error.
    dict(name="fastrom_lda8_rtl", code=[0xA9, 0x5A, 0x6B],
         pc=0x808000, memsel=1, cpu=8, master=54),
    dict(name="fastrom_wram_store", code=[0xA9, 0x5A, 0x8D, 0x00, 0x10, 0x6B],
         pc=0x808000, memsel=1, cpu=12, master=80, write=12),
    dict(name="fastrom_mmio_store", code=[0xA9, 0x00, 0x8D, 0x00, 0x42, 0x6B],
         pc=0x808000, memsel=1, cpu=12, master=78, write=12),
    dict(name="taken_branch", code=[0xA9, 0x01, 0xD0, 0x01, 0xEA, 0x6B],
         cpu=11, master=82),
    dict(name="untaken_branch", code=[0xA9, 0x00, 0xD0, 0x01, 0xEA, 0x6B],
         cpu=12, master=90),
    # Memory loads prevent the constant-Z pass from folding these branches.
    dict(name="dynamic_taken_branch", code=[0xAD, 0x00, 0x10, 0xF0, 0x01, 0xEA, 0x6B],
         cpu=13, master=98),
    dict(name="dynamic_untaken_branch", code=[0xAD, 0x00, 0x10, 0xD0, 0x01, 0xEA, 0x6B],
         cpu=14, master=106),
    dict(name="indexed_page_cross", code=[0xBD, 0xFF, 0x10, 0x6B],
         x=1, cpu=11, master=82),
]


def build(out: Path, cc: str, cases=None) -> Path:
    if cases is None:
        cases = CASES
    bodies = []
    table = []
    for i, case in enumerate(cases):
        pc = case.get("pc", 0x008000)
        code = bytes(case["code"])
        rom = bytearray(0x8000)
        rom[pc & 0x7FFF:(pc & 0x7FFF) + len(code)] = code
        for address, value in case.get("memory", {}).items():
            if address & 0xFFFF >= 0x8000:
                rom[address & 0x7FFF] = value
        for target in case.get("callees", []):
            for m in (0, 1):
                for xf in (0, 1):
                    bodies.append(emit_function(bytes(rom), bank=target >> 16,
                                                start=target & 0xFFFF,
                                                entry_m=m, entry_x=xf))
        name = f"timing_case_{i}_M{case.get('m', 1)}X{case.get('xf', 0)}"
        saved_instruction_timing = os.environ.get("SNESRECOMP_EMIT_INSTRUCTION_TIMING")
        if case.get("instruction_timing"):
            os.environ["SNESRECOMP_EMIT_INSTRUCTION_TIMING"] = (
                f"{pc:06X}:{case.get('m', 1)}:{case.get('xf', 0)}")
        try:
            bodies.append(emit_function(bytes(rom), bank=pc >> 16,
                                        start=pc & 0xFFFF,
                                        entry_m=case.get("m", 1), entry_x=case.get("xf", 0),
                                        func_name=f"timing_case_{i}"))
        finally:
            if saved_instruction_timing is None:
                os.environ.pop("SNESRECOMP_EMIT_INSTRUCTION_TIMING", None)
            else:
                os.environ["SNESRECOMP_EMIT_INSTRUCTION_TIMING"] = saved_instruction_timing
        bodies.append(f"static const uint8_t code_{i}[] = {{" +
                      ",".join(str(x) for x in code) + "};")
        memory = case.get("memory", {})
        bodies.append(f"static const TimingInit init_{i}[] = {{" +
                      (",".join(f"{{{addr},{value}}}" for addr, value in memory.items())
                       or "{0,0}") + "};")
        table.append(f"{{code_{i}, sizeof(code_{i}), {pc}, "
                     f"{case.get('m', 1)}, {case.get('xf', 0)}, {case.get('db', 0)}, "
                     f"{case.get('x', 0)}, {case.get('y', 0)}, {case.get('d', 0)}, "
                     f"{case.get('memsel', 0)}, init_{i}, {len(memory)}, {name}, "
                     f"{int(bool(case.get('instruction_timing')))}, {case.get('status', 4)}" + "}")
    header = """typedef struct TimingInit { uint32 address; uint8 value; } TimingInit;
typedef struct TimingCase {
    const uint8_t *code; int size; uint32 pc;
    uint8 m, xf, db; uint16 x, y, d; uint8 memsel;
    const TimingInit *init; unsigned init_count;
    RecompReturn (*body)(CpuState *);
    int instruction_timing;
    uint8 status;
} TimingCase;
"""
    (out / "timing_cases.inc").write_text(
        header + "\n".join(bodies) + "\nstatic const TimingCase cases[] = {\n" +
        ",\n".join(table) + "\n};\n")
    binary = out / ("timing_test.exe" if os.name == "nt" else "timing_test")
    cmd = shlex.split(cc) + ["-std=c11", "-O1", "-D_POSIX_C_SOURCE=200809L",
        "-DSNESRECOMP_TIER2_TEST=1", "-Wno-unused-parameter",
        "-I", str(out), "-I", str(REPO / "runner/src"),
        "-I", str(REPO / "runner/src/snes"),
        str(REPO / "tests/timing/fixture.c"),
        *[str(REPO / "runner/src/snes" / file) for file in
          ("interp816.c", "tier2_capture.c", "interp_bridge.c", "cx4.c")],
        "-lm", "-o", str(binary)]
    (out / "build-command.json").write_text(json.dumps(cmd, indent=2) + "\n")
    result = subprocess.run(cmd, text=True, capture_output=True)
    (out / "build.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"compiler failed; see {out / 'build.log'}")
    return binary


def require(condition: bool, detail: object) -> None:
    if not condition:
        raise RuntimeError(str(detail))


def run(out: Path, cc: str) -> int:
    binary = build(out, cc)
    results = []
    semantic_keys = ("ok", "a", "x", "y", "s", "p", "d", "db", "m", "xf")
    for index, case in enumerate(CASES):
        pair = {}
        for tier in ("interp", "aot"):
            process = subprocess.run([str(binary), str(index), tier], cwd=out,
                                     text=True, capture_output=True, timeout=10)
            (out / f"{case['name']}-{tier}.log").write_text(process.stdout + process.stderr)
            if process.returncode:
                raise RuntimeError(f"{case['name']} {tier} failed: {process.stderr}")
            pair[tier] = json.loads(process.stdout)
        interp, aot = pair["interp"], pair["aot"]
        require(interp["cpu_cycles"] == case["cpu"], (case, interp))
        require(interp["master_cycles"] == case["master"], (case, interp))
        require(interp["ok"] == 1 and interp["s"] == 0x01FF, (case, interp))
        if "write" in case:
            require(len(interp["writes"]) == 1, (case, interp))
            require(interp["writes"][0]["master"] == case["write"], (case, interp))
        require(all(interp[k] == aot[k] for k in semantic_keys), (case, pair))
        require([(w["address"], w["value"]) for w in interp["writes"]] == [
            (w["address"], w["value"]) for w in aot["writes"]], (case, pair))
        same_clock = interp["master_cycles"] == aot["master_cycles"]
        same_cpu = interp["cpu_cycles"] == aot["cpu_cycles"]
        same_writes = interp["writes"] == aot["writes"]
        result = dict(name=case["name"], **pair, cpu_cycles_equal=same_cpu,
                      master_cycles_equal=same_clock, write_clocks_equal=same_writes,
                      timing_equal=same_cpu and same_clock and same_writes)
        results.append(result)
        print(f"{case['name']}: CPU interp={interp['cpu_cycles']} AOT={aot['cpu_cycles']} "
              f"master interp={interp['master_cycles']} AOT={aot['master_cycles']} "
              f"write-clocks equal={same_writes} "
              f"{'PASS' if result['timing_equal'] else 'TIMING MISMATCH'}", flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    mismatches = sum(not r["timing_equal"] for r in results)
    print("Architectural and interpreter-budget checks passed; "
          f"timing parity: {len(results)-mismatches}/{len(results)}.")
    return int(bool(mismatches))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--bus-timing", action="store_true",
                        help="enable experimental generated bus-clock accounting")
    args = parser.parse_args()
    try:
        # Prevent diagnostic environment settings from changing the tested code
        # or selecting AOT exclusions inside the bridge fixture.
        for key in tuple(os.environ):
            if key.startswith("SNESRECOMP_"):
                os.environ.pop(key)
        if args.bus_timing:
            os.environ["SNESRECOMP_EMIT_BUS_TIMING"] = "1"
        if args.out_dir:
            out = args.out_dir.resolve()
            out.mkdir(parents=True, exist_ok=False)
            return run(out, args.cc)
        with tempfile.TemporaryDirectory(prefix="snesrecomp-timing-") as tmp:
            return run(Path(tmp), args.cc)
    except (OSError, RuntimeError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        print(f"timing harness error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
