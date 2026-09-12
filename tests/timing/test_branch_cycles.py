"""Executable CPU-cycle regression for folded BEQ/BNE, separate from the
known failing master-clock differential. Run directly with Python 3."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import run as timing


def branch_cases():
    cases = []
    for load in (0xA9, 0xA2, 0xA0):  # LDA, LDX, LDY immediate
        for narrow in (0, 1):
            for branch in (0xD0, 0xF0):  # BNE, BEQ
                for nonzero in (False, True):
                    value = (0x80 if narrow else 0x100) if nonzero else 0
                    for offset in (0, 1):  # Include coincident target/fall-through.
                        for fast in (0, 1):
                            taken = nonzero if branch == 0xD0 else not nonzero
                            code = [load, value & 255]
                            if not narrow:
                                code.append(value >> 8)
                            code += [branch, offset] + ([0xEA] if offset else []) + [0x6B]
                            cycles = (2 if narrow else 3) + 2 + int(taken) + 6
                            if offset and not taken:
                                cycles += 2  # NOP on the live fall-through.
                            cases.append(dict(
                                name=f"{load:02X}-{narrow}-{branch:02X}-{value:04X}-{offset}-{fast}",
                                code=code, m=narrow if load == 0xA9 else 1,
                                xf=narrow if load != 0xA9 else 0,
                                pc=0x808000 if fast else 0x008000, memsel=fast,
                                cpu=cycles))
    return cases


class FoldedBranchCycles(unittest.TestCase):
    def test_generated_and_interpreted_cpu_cycles(self):
        cases = branch_cases()
        with tempfile.TemporaryDirectory(prefix="snesrecomp-branch-cycles-") as tmp:
            out = Path(tmp)
            binary = timing.build(out, os.environ.get("CC", "cc"), cases)
            for index, case in enumerate(cases):
                with self.subTest(case=case["name"]):
                    pair = {}
                    for tier in ("interp", "aot"):
                        result = subprocess.run([str(binary), str(index), tier],
                                                cwd=out, capture_output=True,
                                                text=True, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        pair[tier] = json.loads(result.stdout)
                        self.assertEqual(pair[tier]["cpu_cycles"], case["cpu"], pair)
                    # Removing clock fields leaves the architecture and writes.
                    # Master-clock parity remains a separate, failing diagnostic.
                    for state in pair.values():
                        state.pop("cpu_cycles")
                        state.pop("master_cycles")
                    self.assertEqual(pair["interp"], pair["aot"])


if __name__ == "__main__":
    for key in tuple(os.environ):
        if key.startswith("SNESRECOMP_"):
            os.environ.pop(key)
    unittest.main()
