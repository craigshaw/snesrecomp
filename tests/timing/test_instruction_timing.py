"""Native leaf write timestamps and real interpreter/AOT scheduler exits."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run as timing
from test_bus_clocks import memory_cases, indexed_write_cases, boundary_cases
from v2 import bus_timing


class InstructionTiming(unittest.TestCase):
    def test_leaf_writes_and_events(self):
        self.check_leaf_writes_and_events(global_bus_timing=True)

    def test_leaf_writes_and_events_without_global_bus_timing(self):
        self.check_leaf_writes_and_events(global_bus_timing=False)

    def check_leaf_writes_and_events(self, global_bus_timing):
        cases = [c for c in timing.CASES if "branch" not in c["name"]]
        cases += [c for c in memory_cases() if c['name'].split('-')[0] in
                  ('dp', 'dpx', 'abs', 'absx', 'absy', 'long', 'longx')]
        cases += [c for c in indexed_write_cases() if c['code'][0] in (0x9D, 0x99, 0x9E)]
        cases += boundary_cases()
        for xf in (0, 1):
            for load, store in ((0xA2, 0x8E), (0xA0, 0x8C)):
                cases.append(dict(name=f"index-register-{load:02X}-X{xf}", xf=xf,
                                  code=[load, 0x34] + ([0x12] if not xf else []) +
                                  [store, 0x20, 0x10, 0x6B],
                                  cpu=14-2*xf, master=108-16*xf))
        cases += [dict(c, name=c['name']+'-rts', code=c['code'][:-1]+[0x60],
                       master=c['master']-2) for c in cases[:9]]
        cases.append(dict(name="memsel-during-leaf", pc=0x808000,
                          code=[0xA9, 1, 0x8D, 0x0D, 0x42, 0xEA,
                                0xA9, 0x55, 0x8D, 0, 0x10, 0x6B],
                          cpu=20, master=138))
        cases.append(dict(name="irq-mmio", code=[0xA9, 0x55, 0x8D, 0, 0x42, 0x6B],
                          status=0, cpu=12, master=90))
        cases.append(dict(name="fast-irq-mmio", code=[0xA9, 0x55, 0x8D, 0, 0x42, 0x6B],
                          pc=0x808000, memsel=1, status=0, cpu=12, master=78))
        cases.append(dict(name="enable-nmi", code=[0xA9, 0x81, 0x8D, 0, 0x42, 0x6B],
                          cpu=12, master=90))
        for c in cases:
            c["instruction_timing"] = True
        with tempfile.TemporaryDirectory(prefix="snes-leaf-timing-") as temp, patch.dict(
                os.environ, {k: v for k, v in os.environ.items()
                             if not k.startswith("SNESRECOMP_")}, clear=True):
            if global_bus_timing:
                os.environ["SNESRECOMP_EMIT_BUS_TIMING"] = "1"
            os.environ["SNESRECOMP_EMIT_EVENT_CROSSING_AUDIT"] = "1"
            out = Path(temp)
            binary = timing.build(out, os.environ.get("CC", "cc"), cases)
            def run(index, tier, *args):
                p = subprocess.run([str(binary), str(index), tier, *args], cwd=out,
                                   text=True, capture_output=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return json.loads(p.stdout)
            for i, c in enumerate(cases):
                with self.subTest(case=c["name"]):
                    a, b = run(i, "interp"), run(i, "aot")
                    self.assertEqual(a["cpu_cycles"], c["cpu"])
                    self.assertEqual(a["master_cycles"], c["master"])
                    self.assertEqual(a, b)
                    self.assertEqual(b["scope_underflow"], 0)
                    self.assertEqual(b["scope_depth"], 0)
            # JSL: seven 8-clock accesses plus one 6-clock internal cycle.
            # LDA ends at 78, STA ends at 108, and RTL ends at 152.
            leaf = next(i for i, c in enumerate(cases) if c['name'] == 'mmio_store')
            events = 0
            for deadline in (1, 61, 62, 63, 77, 78, 79, 107, 108, 109, 151, 152, 153):
                for resume in ((), ("resume",)):
                    a = run(leaf, "event-interp", str(deadline), *resume)
                    b = run(leaf, "event-aot", str(deadline), *resume)
                    self.assertEqual(a, b, (deadline, resume, a, b))
                    self.assertEqual(b['scope_depth'], 0)
                    self.assertEqual(b['scope_underflow'], 0)
                    if not resume and 62 < deadline <= 78:
                        self.assertEqual(b['resume'], 0x8002)
                        self.assertEqual(b['s'], 0x1FC)
                    if not resume and 78 < deadline <= 108:
                        self.assertEqual(b['resume'], 0x8005)
                        self.assertEqual(b['s'], 0x1FC)
                    events += 1
            for resume in ((), ("resume",)):
                a = run(len(cases)-1, "event-interp", "nmi", *resume)
                b = run(len(cases)-1, "event-aot", "nmi", *resume)
                self.assertEqual(a, b)
                if not resume:
                    self.assertEqual(b['resume'], 0x8005)
                    self.assertEqual(b['nmi'], 1)
                    self.assertEqual(b['s'], 0x1FC)
                events += 1
            for event in ("refresh", "beam-nmi"):
                for resume in ((), ("resume",)):
                    a = run(leaf, "event-interp", event, *resume)
                    b = run(leaf, "event-aot", event, *resume)
                    self.assertEqual(a, b, (event, resume, a, b))
                    if not resume:
                        self.assertEqual(b['resume'], 0x8005)
                        self.assertEqual(b['master_cycles'], 148 if event == 'refresh' else 108)
                        self.assertEqual(b['s'], 0x1FC)
                    events += 1
            irq_leaf = next(i for i, c in enumerate(cases) if c['name'] == 'irq-mmio')
            for selected in (leaf, irq_leaf):  # I set versus I clear.
                for clock in (1, 61, 62, 63, 78, 79, 108, 109, 152):
                    for resume in ((), ("resume",)):
                        a = run(selected, "event-interp", f"irq:{clock}", *resume)
                        b = run(selected, "event-aot", f"irq:{clock}", *resume)
                        self.assertEqual(a, b, (selected, clock, resume, a, b))
                        if selected == irq_leaf and not resume:
                            self.assertEqual(b['irq'], 1)
                            if 78 < clock <= 108:
                                self.assertEqual(b['resume'], 0x8005)
                                self.assertEqual(b['s'], 0x1FC)
                            if 108 < clock <= 152:
                                self.assertEqual(b['resume'], 0x7004)
                                self.assertEqual(b['s'], 0x1FF)
                        events += 1
            fast_irq = next(i for i, c in enumerate(cases) if c['name'] == 'fast-irq-mmio')
            for clock in (62, 63, 74, 75, 98, 99, 140):
                for resume in ((), ("resume",)):
                    a = run(fast_irq, "event-interp", f"irq:{clock}", *resume)
                    b = run(fast_irq, "event-aot", f"irq:{clock}", *resume)
                    self.assertEqual(a, b, (clock, resume, a, b))
                    if not resume and clock > 98:
                        self.assertEqual(b['resume'], 0x7004)
                        self.assertEqual(b['pb'], 0)
                        self.assertEqual(b['s'], 0x1FF)
                    events += 1
            print(f"Instruction timing: {len(cases)} exact leaf comparisons and {events} event/resume comparisons passed")

    def test_selection_fails_closed(self):
        with patch.dict(os.environ, {"SNESRECOMP_EMIT_BUS_TIMING": "1",
                                    bus_timing.INSTRUCTION_ENV: "008000:1:0"}):
            for code in ([0x22, 0x10, 0x80, 0, 0x6B], [0x48, 0x68, 0x6B],
                         [0xB1, 0x40, 0x6B], [0x40], [0xEE, 0, 0x10, 0x6B]):
                with self.assertRaises(ValueError):
                    timing.emit_function(bytes(code) + bytes(32768-len(code)),
                                         bank=0, start=0x8000, entry_m=1, entry_x=0)
            for bad in ("008000", "008000:2:0", "xyzxyz:1:0"):
                os.environ[bus_timing.INSTRUCTION_ENV] = bad
                with self.assertRaises(ValueError):
                    bus_timing.instruction_targets()
            os.environ[bus_timing.INSTRUCTION_ENV] = "008000:1:0"
            os.environ["SNESRECOMP_EMIT_BUS_TIMING"] = ""
            self.assertEqual(bus_timing.instruction_targets(),
                             frozenset({(0x008000, 1, 0)}))


if __name__ == '__main__':
    unittest.main()
