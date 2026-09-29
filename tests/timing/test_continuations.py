"""Scheduler-only block continuations preserve architectural event boundaries."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run as timing
from v2 import continuations


def cases():
    # The loop header is internal. Its PHX/PLX pair also tests interruption
    # inside a local save: interpretation must reach a balanced entry first.
    base = [0xA2, 3, 0, 0xDA, 0xA9, 0x5A, 0x8D, 0, 0x10,
            0xFA, 0xCA, 0xD0, 0xF6, 0x6B]
    result = []
    for fast in (0, 1):
        pc = 0x808000 if fast else 0x8000
        result.append(dict(name=f'local-stack-{fast}', pc=pc, memsel=fast,
                           code=base, continuations=[(pc+3, 1, 0)]))
        # Both M widths are used within a resumed block, including a word RMW.
        code = [0xA2, 3, 0, 0xC2, 0x20, 0x26, 0x40, 0xE2, 0x20,
                0xCA, 0xD0, 0xF7, 0x6B]
        result.append(dict(name=f'width-{fast}', pc=pc, memsel=fast,
                           code=code, continuations=[(pc+3, 1, 0)],
                           memory={0x40:0x7F,0x41:0x80}))
    result.append(dict(name='short-return', short_call=True,
                       code=base[:-1]+[0x60], continuations=[(0x8003,1,0)]))
    # Repeated calls use real guest frames. Test compiled and missing callees.
    for compiled in (False, True):
        result.append(dict(name=f'call-{compiled}',
                           code=[0xA2,3,0,0x22,0,0x81,0,0xCA,0xD0,0xF9,0x6B],
                           continuations=[(0x8003,1,0)],
                           memory={0x8100:0xEA,0x8101:0x6B},
                           **{'callees' if compiled else 'interpreted_callees':[0x8100]}))
    # The caller's return frame changes. A resumed return must use the actual
    # popped PC, not reconstruct a paired host return from the old root.
    result.append(dict(name='rewritten-return',
                       code=[0xA2,3,0,0xA9,5,0x8D,0xFD,1,
                             0xCA,0xD0,0xF8,0x6B],
                       continuations=[(0x8003,1,0)]))
    # A single address has two valid width variants. Lookup and the body
    # selector must use the exact key, including when it differs from root M.
    for value in (0, 1):
        result.append(dict(name=f'exact-width-{value}', x=3,
                           code=[0xAD,0,0x10,0xF0,4,0xE2,0x20,0x80,2,
                                 0xC2,0x20,0xEA,0xCA,0xD0,0xFC,0x6B],
                           memory={0x1000:value},
                           continuations=[(0x800B,0,0),(0x800B,1,0)]))
    for case in result:
        case['instruction_timing'] = True
        case['status'] = 0
    return result


class Continuations(unittest.TestCase):
    def test_fail_closed(self):
        for selection in ('bad', '008000:1:0>008000:1:0',
                          '008000:1:0>018003:1:0',
                          '008000:1:0>008003:1:0,008010:1:0>008003:1:0'):
            with self.subTest(selection=selection), patch.dict(os.environ, {continuations.ENV:selection}):
                with self.assertRaises(ValueError): continuations.selections()
        # Mid-instruction, wrong width, non-block entry, absent timing,
        # and a JMP tail must not become continuation entries.
        for code, point, timed in [
                ([0xA2,3,0,0xCA,0xD0,0xFD,0x6B], '008004:1:0', True),
                ([0xA2,3,0,0xCA,0xD0,0xFD,0x6B], '008003:0:0', True),
                ([0x48,0xD0,0,0x68,0x6B], '008004:1:0', True),
                ([0xA2,3,0,0xCA,0xD0,0xFD,0x6B], '008003:1:0', False),
                ([0xA2,3,0,0xCA,0xD0,0xFD,0x5C,0,0x81,0], '008003:1:0', True)]:
            with patch.dict(os.environ, {continuations.ENV:f'008000:1:0>{point}',
                     'SNESRECOMP_EMIT_INSTRUCTION_TIMING':'008000:1:0' if timed else ''}):
                with self.subTest(code=code,point=point), self.assertRaises(ValueError):
                    timing.emit_function(bytes(code)+bytes(32768-len(code)),
                                         bank=0,start=0x8000,entry_m=1,entry_x=0)

    def test_event_parity(self):
        with tempfile.TemporaryDirectory(prefix='snes-resume-') as temp, patch.dict(
                os.environ, {k:v for k,v in os.environ.items()
                             if not k.startswith('SNESRECOMP_')}, clear=True):
            tests=cases(); binary=timing.build(Path(temp),os.environ.get('CC','cc'),tests)
            checks=0; total_entries=0
            for i, case in enumerate(tests):
                for event in ('63','84','100','120','150','180','220',
                              'refresh','beam-nmi','irq:120'):
                    for resume in ((), ('resume',), ('repeat',)):
                        results=[]
                        for tier in ('event-interp','event-aot'):
                            p=subprocess.run([str(binary),str(i),tier,event,*resume],
                                             capture_output=True,text=True,timeout=10)
                            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                            result=json.loads(p.stdout)
                            total_entries += result.pop('continuation_entries')
                            results.append(result)
                        with self.subTest(case=case['name'],event=event,resume=resume):
                            self.assertEqual(*results)
                        checks+=1
            # Disabling LLE bounce must disable internal continuations too.
            env=dict(os.environ, SNESRECOMP_LLE_BOUNCE='0')
            disabled=[]
            for tier in ('event-interp','event-aot'):
                p=subprocess.run([str(binary),'0',tier,'120','repeat'],env=env,
                                 capture_output=True,text=True,timeout=10)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                disabled.append(json.loads(p.stdout))
            self.assertEqual(*disabled)
            self.assertEqual(disabled[1]['continuation_entries'],0)
            self.assertGreater(total_entries, 0)
            print(f'Continuations: {checks} event comparisons, {total_entries} native entries')


if __name__ == '__main__': unittest.main()
