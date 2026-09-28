"""Direct native calls commit before transferring control to their callee."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run as timing


class CallInstructionTiming(unittest.TestCase):
    maxDiff = None
    def test_calls_and_instruction_boundaries(self):
        self.check_calls(False)

    def test_calls_with_global_bus_timing(self):
        self.check_calls(True)

    def check_calls(self, global_bus, tail=False):
        cases = []
        for long in (False, True):
            for fast in (False, True):
                for route in ('compiled', 'interpreted', 'denied'):
                    interpreted = route == 'interpreted'
                    for m in (0, 1):
                        bank = 0x80 if fast else 0
                        # The skipped call exercises a load/branch deadline
                        # with an otherwise reachable call in the same body.
                        for skip in (False, True):
                            target = 0xA100 + len(cases) * 0x10
                            callee_bank = bank ^ 0x80 if long else bank
                            target24 = callee_bank << 16 | target
                            call = ([0x5C if tail else 0x22, target & 255, target >> 8, callee_bank] if long or tail
                                    else [0x20, target & 255, target >> 8])
                            code = ([0xAD, 0, 0x10] + [0xF0, len(call)] + call +
                                    [0x09, 0x80] + ([0] if not m else []) +
                                    [0x8D, 0x20, 0x10, 0x6B])
                            cases.append(dict(
                                name=f"call-{long}-{fast}-{route}-{m}-{skip}",
                                pc=(bank << 16) | 0x8000, m=m,
                                xf=m ^ int(interpreted), memsel=int(fast),
                                code=code, instruction_timing=True, status=0,
                                callees=[] if interpreted else [target24],
                                interpreted_callees=[target24] if interpreted else [],
                                denied_callees=[target24] if route == 'denied' else [],
                                memory={0x1000: int(not skip), target24: 0xEA,
                                        target24 + 1: 0x6B if long or tail else 0x60}))
        with tempfile.TemporaryDirectory(prefix="snes-call-timing-") as temp, patch.dict(
                os.environ, {k: v for k, v in os.environ.items()
                             if not k.startswith("SNESRECOMP_")}, clear=True):
            out = Path(temp)
            if global_bus:
                os.environ['SNESRECOMP_EMIT_BUS_TIMING'] = '1'
            os.environ['SNESRECOMP_EMIT_AOT_DENY_GATE'] = '1'
            binary = timing.build(out, os.environ.get("CC", "cc"), cases)
            def run(i, tier, *args):
                deny = out / 'denied.txt'
                deny.write_text(''.join(f'{pc:06X}\n' for pc in cases[i]['denied_callees']))
                env = dict(os.environ, SNESRECOMP_LLE_INTERP_TARGET_FILE=str(deny))
                p = subprocess.run([str(binary), str(i), tier, *args], cwd=out,
                                   env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return json.loads(p.stdout)
            events = 0
            for i, c in enumerate(cases):
                with self.subTest(case=c['name']):
                    self.assertEqual(run(i, 'interp'), run(i, 'aot'))
                for event in [*map(str, range(62, 282, 9)), 'refresh', 'beam-nmi',
                              *[f'irq:{n}' for n in (63, 94, 125, 157, 186, 230)]]:
                    for resume in ((), ('resume',)):
                        with self.subTest(case=c['name'], event=event, resume=resume):
                            self.assertEqual(run(i, 'event-interp', event, *resume),
                                             run(i, 'event-aot', event, *resume))
                        events += 1
            print(f"{'Tail' if tail else 'Call'} instruction timing: checked {len(cases)} complete and {events} event/resume comparisons")


if __name__ == '__main__':
    unittest.main()
