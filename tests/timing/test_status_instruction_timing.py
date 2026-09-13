"""Width changes and simple ALU leaves retain interpreter timing and resumes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run as timing


def cases():
    result = []
    for m in (0, 1):
        for fast in (0, 1):
            for value in (0, 0x7FFF, 0xFFFF):
                operand = lambda opcode, n: [opcode, n & 255] + ([n >> 8] if not m else [])
                result.append(dict(
                    name=f"logical-{m}-{fast}-{value}", m=m,
                    pc=0x808000 if fast else 0x008000, memsel=fast,
                    code=operand(0xA9, value) + operand(0x49, 0x8000) +
                         operand(0x29, 0xFFFF) + [0x24, 0x40, 0x1A, 0x8D, 0, 0x10, 0x6B],
                    memory={0x40: 0xC0, 0x41: 0xC0}))
    for m in (0, 1):
        for xf in (0, 1):
            for value in (0, 0x8000, 0xFFFF):
                result.append(dict(
                    name=f"register-{m}-{xf}-{value}", m=m, xf=xf, y=value,
                    code=[0x88, 0x98, 0xEB, 0x1A, 0x18, 0x69, 1] +
                         ([0] if not m else []) + [0x8D, 0, 0x10, 0x6B]))
    # A's high byte survives M=1, including TYA, accumulator INC and XBA.
    # Index-width changes remain outside the selected instruction mode.
    result.append(dict(name="width-transitions", status=5, code=[
        0xC2, 0x20, 0xA9, 0x5A, 0xA5, 0xA0, 0xFE, 0xCA,
        0xA2, 0x34, 0x12, 0xE2, 0x20, 0x98, 0xEB, 0x1A, 0x88,
        0x18, 0x69, 1, 0x8D, 0, 0x10, 0xC2, 0x20, 0x98,
        0x8D, 2, 0x10, 0x8C, 4, 0x10, 0x8E, 6, 0x10, 0x6B]))
    result.append(dict(name="status-irq", status=4, code=[
        0xC2, 4, 0xEA, 0xE2, 4, 0xEA, 0xC2, 4, 0x6B]))
    # BIT immediate changes Z only, unlike the memory form above.
    result.append(dict(name="bit-immediate", status=0xC5,
                       code=[0xA9, 0x80, 0x89, 0x80, 0x6B]))
    for fast in (0, 1):
        # REP/SEP each take two fetches and one internal cycle. The 16-bit
        # immediate load takes three fetches; INC A takes one of each.
        result.append(dict(name=f"width-budget-{fast}",
                           pc=0x808000 if fast else 0x008000, memsel=fast,
                           code=[0xC2, 0x20, 0xA9, 0x34, 0x12,
                                 0xE2, 0x20, 0x1A, 0x6B],
                           cpu=17, master=108 if fast else 126))
    for c in result:
        c["instruction_timing"] = True
    return result


class StatusInstructionTiming(unittest.TestCase):
    def test_index_width_changes_fail_closed(self):
        with patch.dict(os.environ, {"SNESRECOMP_EMIT_INSTRUCTION_TIMING": "008000:1:0"}):
            for opcode in (0xC2, 0xE2):
                with self.assertRaises(ValueError):
                    timing.emit_function(bytes([opcode, 0x10, 0x6B]) + bytes(32765),
                                         bank=0, start=0x8000, entry_m=1, entry_x=0)

    def test_status_alu_and_scheduler(self):
        with tempfile.TemporaryDirectory(prefix="snes-status-timing-") as temp, patch.dict(
                os.environ, {k: v for k, v in os.environ.items()
                             if not k.startswith("SNESRECOMP_")}, clear=True):
            out = Path(temp)
            tests = cases()
            binary = timing.build(out, os.environ.get("CC", "cc"), tests)

            def run(i, tier, *args):
                p = subprocess.run([str(binary), str(i), tier, *args],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return json.loads(p.stdout)

            for i, c in enumerate(tests):
                with self.subTest(case=c["name"]):
                    a, b = run(i, "interp"), run(i, "aot")
                    self.assertEqual(a, b)
                    if "cpu" in c:
                        self.assertEqual(a["cpu_cycles"], c["cpu"])
                        self.assertEqual(a["master_cycles"], c["master"])
            width = next(i for i, c in enumerate(tests) if c["name"] == "width-transitions")
            irq = next(i for i, c in enumerate(tests) if c["name"] == "status-irq")
            checks = 0
            for i, events in (
                    (width, ("63", "83", "84", "85", "143", "144", "145",
                             "171", "205", "249", "300", "400", "refresh", "beam-nmi")),
                    (irq, ("irq:1", "irq:83", "irq:84", "irq:85", "irq:120"))):
                for event in events:
                    for resume in ((), ("resume",)):
                        with self.subTest(case=tests[i]["name"], event=event, resume=resume):
                            self.assertEqual(run(i, "event-interp", event, *resume),
                                             run(i, "event-aot", event, *resume))
                        checks += 1
            print(f"Status instruction timing: checked {len(tests)} complete and {checks} event comparisons")


if __name__ == "__main__":
    unittest.main()
