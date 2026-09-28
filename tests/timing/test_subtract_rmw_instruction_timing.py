"""Selected subtract and byte direct-page RMW timing matches the interpreter."""
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
        top = 0xFF if m else 0xFFFF
        sign = (top + 1) // 2
        for mode in ("immediate", "absolute"):
            for fast in (0, 1):
                for a, operand, carry, decimal in (
                        (0, 1, 1, 0), (sign, 1, 1, 0),
                        (sign - 1, top, 1, 0), (top, top, 0, 0),
                        (0, 1, 1, 8), (0x99 if m else 0x9999, 1, 0, 8)):
                    load = [0xA9, a & 255] + ([] if m else [a >> 8])
                    subtract = ([0xE9, operand & 255] + ([] if m else [operand >> 8])
                                if mode == "immediate" else [0xED, 0x40, 0x10])
                    result.append(dict(
                        name=f"subtract-{m}-{mode}-{fast}-{a}-{carry}-{decimal}",
                        m=m, pc=0x808000 if fast else 0x8000, memsel=fast,
                        status=decimal, code=load + [0x38 if carry else 0x18] +
                        subtract + [0x8D, 0, 0x10, 0x6B],
                        memory={0x1040: operand & 255, 0x1041: operand >> 8}))
    for opcode in (0xE6, 0xC6):
        for value in (0, 0x7F, 0x80, 0xFF):
            for d in (0, 1, 0xFFFF):
                address = (d + 0x40) & 0xFFFF
                result.append(dict(
                    name=f"rmw-{opcode:02x}-{value}-{d}", d=d, status=0x49,
                    pc=0x808000 if d == 1 else 0x8000, memsel=int(d == 1),
                    code=[opcode, 0x40, 0xA5, 0x40, 0x8D, 0, 0x10, 0x6B],
                    memory={address: value}))
    result.append(dict(name="width-stack-loop", x=2, status=0, memory={0x40: 0},
                       code=[0xC2, 0x20, 0xA9, 0, 0x80, 0x38, 0xE9, 1, 0,
                             0xE2, 0x20, 0x48, 0xE6, 0x40, 0xC6, 0x40, 0x68,
                             0xCA, 0xD0, 0xF7, 0xC2, 0x20, 0x8D, 0, 0x10, 0x6B]))
    for opcode in (0x06, 0x26):
        for value in (0, 0x7F, 0x80, 0xFF):
            for carry in (0, 1):
                for d in (0, 1, 0xFFFF):
                    result.append(dict(
                        name=f"shift-{opcode:02x}-{value}-{carry}-{d}", d=d,
                        status=0x48 | carry, memsel=int(d == 1),
                        pc=0x808000 if d == 1 else 0x8000,
                        code=[opcode, 0x40, 0xA5, 0x40, 0x8D, 0, 0x10, 0x6B],
                        memory={(d + 0x40) & 0xFFFF: value}))
    for a, operand, carry, decimal in ((0, 1, 1, 0), (0x80, 1, 1, 0),
                                       (0x7F, 0xFF, 1, 0), (0xFF, 0xFF, 0, 0),
                                       (0, 1, 1, 8), (0x99, 1, 0, 8)):
        for d in (0, 1):
            result.append(dict(name=f"subtract-dp-{a}-{carry}-{decimal}-{d}",
                               status=decimal, d=d, memory={d+0x40: operand},
                               code=[0xA9, a, 0x38 if carry else 0x18, 0xE5, 0x40,
                                     0x8D, 0, 0x10, 0x6B]))
    result.append(dict(name="byte-carry-chain", y=2, status=0,
                       memory={0x40: 0x81, 0x41: 0x7F, 0x42: 0, 0x43: 1},
                       code=[0x06, 0x40, 0x26, 0x41, 0x26, 0x42, 0xA5, 0x42,
                             0x38, 0xE5, 0x43, 0x85, 0x42, 0x88, 0x10, 0xF0,
                             0x6B]))
    for c in result:
        c["instruction_timing"] = True
    return result


class SubtractRmwTiming(unittest.TestCase):
    def test_untested_rmw_forms_fail_closed(self):
        with patch.dict(os.environ, {"SNESRECOMP_EMIT_INSTRUCTION_TIMING": "008000:1:0"}):
            for code in ([0xC2, 0x20, 0xE6, 0x40, 0x6B],
                         [0xEE, 0x40, 0, 0x6B], [0xD6, 0x40, 0x6B],
                         [0xC2, 0x20, 0x26, 0x40, 0x6B]):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    timing.emit_function(bytes(code) + bytes(32768-len(code)),
                                         bank=0, start=0x8000, entry_m=1, entry_x=0)

    def test_complete_and_event_parity(self):
        with tempfile.TemporaryDirectory(prefix="snes-subtract-rmw-") as temp, patch.dict(
                os.environ, {k: v for k, v in os.environ.items()
                             if not k.startswith("SNESRECOMP_")}, clear=True):
            tests = cases()
            binary = timing.build(Path(temp), os.environ.get("CC", "cc"), tests)

            def run(i, tier, *args):
                p = subprocess.run([str(binary), str(i), tier, *args],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return json.loads(p.stdout)

            for i, c in enumerate(tests):
                with self.subTest(case=c["name"]):
                    self.assertEqual(run(i, "interp"), run(i, "aot"))
            selected = [0, 23, 24, 47, 48, 60, 72, 73, 97, 121, len(tests)-1]
            checks = 0
            for i in selected:
                for event in ("63", "84", "100", "120", "160", "220",
                              "refresh", "beam-nmi", "irq:100"):
                    for resume in ((), ("resume",)):
                        with self.subTest(case=tests[i]["name"], event=event, resume=resume):
                            self.assertEqual(run(i, "event-interp", event, *resume),
                                             run(i, "event-aot", event, *resume))
                        checks += 1
            print(f"Subtract/RMW timing: {len(tests)} complete and {checks} event comparisons")


if __name__ == "__main__":
    unittest.main()
