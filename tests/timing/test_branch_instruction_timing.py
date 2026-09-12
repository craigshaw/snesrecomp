"""Selected local branches and arithmetic match real interpreter execution."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run as timing
from test_branch_cycles import branch_cases
from test_bus_clocks import memory_cases


def cases():
    result = branch_cases()
    for opcode, flag, sense in ((0x10, 0x80, 0), (0x30, 0x80, 1),
                                (0x50, 0x40, 0), (0x70, 0x40, 1),
                                (0x90, 0x01, 0), (0xB0, 0x01, 1),
                                (0xD0, 0x02, 0), (0xF0, 0x02, 1)):
        for bit in (0, 1):
            for fast in (0, 1):
                for displacement in (0, 2):
                    taken = bit == sense
                    load = bool(displacement and not taken)
                    speed = 6 if fast else 8
                    result.append(dict(
                        name=f"dynamic-{opcode:02X}-{bit}-{fast}-{displacement}",
                        code=[opcode, displacement] + ([0xA9, 0x55] if displacement else []) + [0x6B],
                        pc=0x8080FC if fast else 0x0080FC, memsel=fast,
                        status=4 | (flag if bit else 0),
                        cpu=8+int(taken)+2*load,
                        master=3*speed+36+6*taken+2*speed*load))
    for opcode in (0x80, 0x82):
        for fast in (0, 1):
            speed = 6 if fast else 8
            result.append(dict(name=f"unconditional-{opcode:02X}-{fast}",
                               code=[opcode, 2] + ([0] if opcode == 0x82 else []) +
                                    [0xA9, 0x55, 0x6B],
                               pc=0x8080FC if fast else 0x0080FC, memsel=fast,
                               cpu=9 if opcode == 0x80 else 10,
                               master=(3 if opcode == 0x80 else 4)*speed+42))
    for m in (0, 1):
        for decimal in (0, 1):
            for carry in (0, 1):
                for lhs, rhs in ((0, 0), (1, 1), (0x49, 0x51), (0x99, 1),
                                 (0x7F, 1), (0xFF, 1), (0x9999, 1), (0x7FFF, 1)):
                    mask = 255 if m else 65535
                    operand = lambda op, value: [op, value & 255] + ([value >> 8 & 255] if not m else [])
                    code = operand(0xA9, lhs) + operand(0x69, rhs) + operand(0xC9, mask) + [0x0A, 0x8D, 0, 0x10, 0x6B]
                    result.append(dict(name=f"arithmetic-{m}-{decimal}-{carry}-{lhs}-{rhs}",
                                       code=code, m=m, status=4 | decimal*8 | carry,
                                       cpu=22-4*m, master=170-32*m))
    result.append(dict(name="backward-native-page-cross", pc=0x0080FC,
                       code=[0xAD, 0, 0x10, 0x69, 1, 0xC9, 3, 0x8D, 0, 0x10,
                             0xD0, 0xF4, 0x6B], status=0, cpu=50, master=392))
    # Reuse known read budgets across all accepted memory addressing forms.
    for c in memory_cases():
        if '-read-' not in c['name'] or c['name'].split('-')[0] not in ('dp', 'dpx', 'abs', 'absx', 'absy', 'long', 'longx'):
            continue
        for opcode_base in (0x60, 0xC0):  # ADC and CMP share LDA addressing offsets.
            code = list(c['code'])
            code[0] = (code[0] & 0x1F) | opcode_base
            for status in (4, 5, 12, 13):
                result.append(dict(c, name=f"read-{opcode_base:02X}-{status}-"+c['name'],
                                   code=code, status=status))
    for c in result:
        c['instruction_timing'] = True
    return result


class BranchInstructionTiming(unittest.TestCase):
    def test_branches_arithmetic_and_scheduler_exits(self):
        self.check_branches_arithmetic_and_scheduler_exits(False)

    def test_branches_arithmetic_with_global_bus(self):
        self.check_branches_arithmetic_and_scheduler_exits(True)

    def check_branches_arithmetic_and_scheduler_exits(self, global_bus):
        with tempfile.TemporaryDirectory(prefix="snes-branch-instruction-") as temp, patch.dict(
                os.environ, {k: v for k, v in os.environ.items()
                             if not k.startswith("SNESRECOMP_")}, clear=True):
            os.environ["SNESRECOMP_EMIT_EVENT_CROSSING_AUDIT"] = "1"
            if global_bus:
                os.environ["SNESRECOMP_EMIT_BUS_TIMING"] = "1"
            out = Path(temp)
            tests = cases()
            binary = timing.build(out, os.environ.get('CC', 'cc'), tests)
            def run(i, tier, *args):
                p = subprocess.run([str(binary), str(i), tier, *args], cwd=out,
                                   text=True, capture_output=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
                return json.loads(p.stdout)
            events = 0
            for i, c in enumerate(tests):
                with self.subTest(case=c['name']):
                    a, b = run(i, 'interp'), run(i, 'aot')
                    self.assertEqual(a['cpu_cycles'], c['cpu'])
                    if 'master' in c:
                        self.assertEqual(a['master_cycles'], c['master'])
                    self.assertEqual(a, b)
                # Cross a taken/untaken branch, page boundary, folded branch,
                # or ALU flag change, then resume from the precise guest PC.
                if i in (0, 4, 96, 97, 104, 105, 112, 113, 160, 162, 164, 180, 228):
                    for deadline in range(62, 210, 7):
                        for resume in ((), ('resume',)):
                            with self.subTest(case=c['name'], deadline=deadline, resume=resume):
                                self.assertEqual(run(i, 'event-interp', str(deadline), *resume),
                                                 run(i, 'event-aot', str(deadline), *resume))
                            events += 1
            print(f"Branch instruction timing: {len(tests)} complete comparisons and {events} scheduler comparisons passed")


if __name__ == '__main__':
    unittest.main()
