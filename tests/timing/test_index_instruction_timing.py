"""Index compare and increment preserve flags, clocks and event resumes."""
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
        for xf in (0, 1):
            top = 0xFF if xf else 0xFFFF
            limit = top // 2 + 1
            for mode in ("immediate", "direct", "absolute"):
                operand = ([0xE0, limit] if xf else [0xE0, limit & 255, limit >> 8])
                if mode == "direct":
                    operand = [0xE4, 0x40]
                elif mode == "absolute":
                    operand = [0xEC, 0x40, 0]
                for value in (0, limit, top):
                    result.append(dict(
                        name=f"compare-{m}-{xf}-{mode}-{value}", m=m, xf=xf,
                        x=value, d=1 if mode == "direct" else 0,
                        memory=({0x41: limit & 255, 0x42: limit >> 8}
                                if mode == "direct" else
                                {0x40: limit & 255, 0x41: limit >> 8}),
                        code=operand + [0x8E, 0, 0x10, 0x6B]))
            for value in (0, top // 2, top):
                result.append(dict(name=f"increment-{m}-{xf}-{value}", m=m,
                                   xf=xf, x=value,
                                   code=[0xE8, 0x8E, 0, 0x10, 0x6B]))
    # Repeated compare, branch and indexed stores exercise loop exits as well
    # as an event followed by resumed interpreter execution inside the loop.
    for fast in (0, 1):
        result.append(dict(name=f"loop-{fast}", pc=0x808000 if fast else 0x8000,
                           memsel=fast, x=0, code=[
                               0xA9, 0x5A, 0xE0, 3, 0, 0xB0, 6,
                               0x9D, 0, 0x10, 0xE8, 0x80, 0xF5, 0x6B]))
    for c in result:
        c["instruction_timing"] = True
        c["status"] = 0  # Allow the scheduled IRQ to stop the fixture.
    return result


class IndexInstructionTiming(unittest.TestCase):
    def test_complete_and_event_parity(self):
        with tempfile.TemporaryDirectory(prefix="snes-index-timing-") as temp, patch.dict(
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
            for i, c in enumerate(tests):
                with self.subTest(case=c["name"]):
                    self.assertEqual(run(i, "interp"), run(i, "aot"))
                events = ("63", "84", "85", "110", "refresh", "beam-nmi", "irq:85")
                if c["name"].startswith("loop"):
                    events += ("160", "240", "320", "400")
                for event in events:
                    for resume in ((), ("resume",)):
                        with self.subTest(case=c["name"], event=event, resume=resume):
                            self.assertEqual(run(i, "event-interp", event, *resume),
                                             run(i, "event-aot", event, *resume))
                        checks += 1
            print(f"Index instruction timing: {len(tests)} complete and {checks} event comparisons")


if __name__ == "__main__":
    unittest.main()
