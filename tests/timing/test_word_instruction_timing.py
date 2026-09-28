"""Selected word RMW, rotates and local X saves preserve bus and resume state."""
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
    for opcode in (0x26, 0x46):  # ROL/LSR direct page, M0 only.
        for value in (0, 1, 0x7FFF, 0x8000, 0xFFFF):
            for carry in (0, 1):
                for d in (0, 1, 0xFFBF):  # Last case wraps the high byte.
                    address = (d + 0x40) & 65535
                    result.append(dict(
                        name=f"rmw-{opcode:02x}-{value}-{carry}-{d}", m=0, d=d,
                        status=0x48 | carry, pc=0x808000 if d == 1 else 0x8000,
                        memsel=int(d == 1),
                        code=[opcode, 0x40, 0x6B],
                        memory={address: value & 255, (address+1)&65535: value >> 8}))
    for value in (0, 1, 0x7FFF, 0x8000, 0xFFFF):
        for carry in (0, 1):
            result.append(dict(name=f"ror-{value}-{carry}", m=0, status=0x48|carry,
                               code=[0xA9, value&255, value>>8, 0x6A,
                                     0x8D, 0, 0x10, 0x6B]))
    for m in (0, 1):
        for fast in (0, 1):
            for value in (0, 0x7FFF, 0x8000, 0xFFFF):
                result.append(dict(
                    name=f"x-stack-{m}-{fast}-{value}", m=m, x=value, status=0x49,
                    pc=0x808000 if fast else 0x8000, memsel=fast,
                    code=[0xDA, 0xA2, 0x34, 0x12, 0xDA, 0xA2, 0, 0,
                          0xFA, 0x8E, 0x20, 0x10, 0xFA, 0x8E, 0x22, 0x10, 0x6B]))
    for a, operand, status in ((0, 1, 1), (0x8000, 1, 1), (0x7FFF, 0xFFFF, 1),
                                (0xFFFF, 0xFFFF, 0), (0, 1, 9), (0x9999, 1, 8)):
        for d in (0, 1):
            result.append(dict(name=f"subtract-{a}-{operand}-{status}-{d}", m=0,
                               status=status, d=d,
                               memory={d+0x40:operand&255,d+0x41:operand>>8},
                               code=[0xA9,a&255,a>>8,0xE5,0x40,0x8D,0,0x10,0x6B]))
    result.append(dict(name="mixed-stack-width", x=0xABCD, status=0,
                       code=[0xA9, 0x7F, 0x48, 0xDA, 0xC2, 0x20,
                             0xA9, 0, 0x80, 0x38, 0x6A, 0xFA,
                             0xE2, 0x20, 0x68, 0x8D, 0, 0x10, 0x8E, 2, 0x10, 0x6B]))
    # Three iterations keep the bounded fixture's write journal below 32.
    result.append(dict(name="carry-loop", m=0, x=2, status=0,
                       memory={0x40:0x81,0x41:0x7F,0x42:0xFE,0x43:0xFF},
                       code=[0x38, 0x26, 0x40, 0x46, 0x42, 0xDA,
                             0xA5, 0x40, 0xE5, 0x42, 0x6A, 0xFA,
                             0xCA, 0x10, 0xF1, 0x6B]))
    for c in result:
        c['instruction_timing'] = True
    return result


class WordInstructionTiming(unittest.TestCase):
    def test_unsupported_paths_fail_closed(self):
        rejected = [
            [0xFA, 0x6B],  # Consumes caller's frame.
            [0x48, 0xFA, 0x6B],  # Only one byte available to PLX.
            [0xDA, 0x6B],  # Word save remains at return.
            [0xDA, 0x22, 0, 0x81, 0, 0xFA, 0x6B],  # Save across call.
            [0xAD, 0, 0x10, 0xD0, 1, 0xDA, 0xFA, 0x6B],  # Unequal join.
            [0xDA, 0xD0, 0xFD, 0xFA, 0x6B],  # Growing stack loop.
            [0xC2, 0x20, 0xE6, 0x40, 0x6B],  # Word INC remains excluded.
            [0xC2, 0x20, 0x6E, 0x40, 0, 0x6B],  # Absolute ROR excluded.
        ]
        with patch.dict(os.environ, {'SNESRECOMP_EMIT_INSTRUCTION_TIMING':'008000:1:0'}):
            for code in rejected:
                with self.subTest(code=code), self.assertRaises(ValueError):
                    timing.emit_function(bytes(code)+bytes(32768-len(code)),
                                         bank=0,start=0x8000,entry_m=1,entry_x=0)
        with patch.dict(os.environ, {'SNESRECOMP_EMIT_INSTRUCTION_TIMING':'008000:1:1'}):
            with self.assertRaises(ValueError):
                timing.emit_function(bytes([0xDA,0xFA,0x6B])+bytes(32765),
                                     bank=0,start=0x8000,entry_m=1,entry_x=1)

    def test_complete_and_event_parity(self):
        with tempfile.TemporaryDirectory(prefix='snes-word-timing-') as temp, patch.dict(
                os.environ, {k:v for k,v in os.environ.items()
                             if not k.startswith('SNESRECOMP_')}, clear=True):
            tests=cases();binary=timing.build(Path(temp),os.environ.get('CC','cc'),tests)
            def run(i,tier,*args):
                p=subprocess.run([str(binary),str(i),tier,*args],capture_output=True,
                                 text=True,timeout=10)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                return json.loads(p.stdout)
            for i,c in enumerate(tests):
                with self.subTest(case=c['name']):
                    self.assertEqual(run(i,'interp'),run(i,'aot'))
            checks=0
            for i in [1,2,31,32,61,70,86,len(tests)-2,len(tests)-1]:
                for event in ('63','84','100','120','140','180','220','300',
                              'refresh','beam-nmi','irq:120'):
                    for resume in ((),('resume',)):
                        with self.subTest(case=tests[i]['name'],event=event,resume=resume):
                            self.assertEqual(run(i,'event-interp',event,*resume),
                                             run(i,'event-aot',event,*resume))
                        checks+=1
            print(f'Word timing: {len(tests)} complete and {checks} event comparisons')


if __name__=='__main__':
    unittest.main()
