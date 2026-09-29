"""Balanced byte accumulator stacks retain interpreter timing on resume."""
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
    for xf in (0, 1):
        for fast in (0, 1):
            for value, index in ((0, 0), (0x7F, 1), (0x80, 0x80 if xf else 0x8000),
                                 (0xFF, 0xFF if xf else 0xFFFF)):
                result.append(dict(
                    name=f"stack-{xf}-{fast}-{value}", xf=xf, x=index,
                    pc=0x808000 if fast else 0x008000, memsel=fast,
                    # Two nested brackets expose byte order and live stack
                    # state when an event stops between push and pull.
                    code=[0xA9, value, 0x48, 0xA9, value ^ 0xFF, 0x48,
                          0xA9, 0, 0x68, 0x8D, 0x20, 0x10, 0x68,
                          0x8D, 0x21, 0x10, 0xCA, 0x8E, 0x22, 0x10, 0x6B]))
    result.append(dict(name="preserve-high-byte", code=[
        0xC2, 0x20, 0xA9, 0x34, 0xAB, 0xE2, 0x20,
        0x48, 0xA9, 0xFE, 0x68, 0xC2, 0x20, 0x8D, 0x20, 0x10,
        0xE2, 0x20, 0x6B]))
    result.append(dict(name="balanced-loop", x=2, code=[
        0xBD, 0x40, 0x10, 0x48, 0x49, 0xFF, 0x9D, 0x50, 0x10,
        0x68, 0x9D, 0x60, 0x10, 0xCA, 0x10, 0xF0, 0x6B],
        memory={0x1040: 0x80, 0x1041: 0x7F, 0x1042: 0xFF}))
    for case in result:
        case.update(instruction_timing=True, status=0)
    return result


class StackInstructionTiming(unittest.TestCase):
    def test_unsupported_stack_paths_fail_closed(self):
        rejected = [
            [0x68, 0x6B],  # Pulling the caller's frame.
            [0x48, 0x6B],  # Returning with live local data.
            [0x48, 0xD0, 0xFD, 0x68, 0x6B],  # Growing stack loop.
            [0xAD, 0, 0x10, 0xD0, 1, 0x48, 0x68, 0x6B],  # Unequal join.
            [0xC2, 0x20, 0x48, 0x6B],  # Word save remains at return.
            [0x48, 0x22, 0, 0x81, 0, 0x68, 0x6B],  # Live data across call.
        ]
        with patch.dict(os.environ, {"SNESRECOMP_EMIT_INSTRUCTION_TIMING": "008000:1:0"}):
            for code in rejected:
                with self.subTest(code=code), self.assertRaises(ValueError):
                    timing.emit_function(bytes(code) + bytes(32768-len(code)),
                                         bank=0, start=0x8000, entry_m=1, entry_x=0)

    def test_complete_and_event_parity(self):
        with tempfile.TemporaryDirectory(prefix="snes-stack-timing-") as temp, patch.dict(
                os.environ, {k: v for k, v in os.environ.items()
                             if not k.startswith("SNESRECOMP_")}, clear=True):
            tests = cases()
            binary = timing.build(Path(temp), os.environ.get("CC", "cc"), tests)

            def run(i, tier, *args):
                p = subprocess.run([str(binary), str(i), tier, *args],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return json.loads(p.stdout)

            checks = 0
            for i, case in enumerate(tests):
                with self.subTest(case=case["name"]):
                    self.assertEqual(run(i, "interp"), run(i, "aot"))
                for event in ("63", "84", "100", "115", "140", "165", "190",
                              "220", "250", "refresh", "beam-nmi", "irq:115"):
                    for resume in ((), ("resume",)):
                        with self.subTest(case=case["name"], event=event, resume=resume):
                            self.assertEqual(run(i, "event-interp", event, *resume),
                                             run(i, "event-aot", event, *resume))
                        checks += 1
            print(f"Stack instruction timing: {len(tests)} complete and {checks} event comparisons")


if __name__ == "__main__":
    unittest.main()
