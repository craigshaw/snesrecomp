"""Timed table scans can resume with statically proven guest register saves."""
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
    for fast in (0, 1):
        common = dict(instruction_timing=True, status=0,
                      pc=0x808000 if fast else 0x8000, memsel=fast)
        for value in (0, 1, 0x8000, 0xFFFF):
            for d in (0, 1, 0xFFBF):
                address = (d + 0x40) & 65535
                result.append(dict(common, name=f'dec-{fast}-{value}-{d}', m=0, d=d,
                                   code=[0xC6,0x40,0x6B],
                                   memory={address:value&255,(address+1)&65535:value>>8}))
        for xf in (0, 1):
            for y in (0x7F, 0xFF, 0x7FFF, 0xFFFF):
                result.append(dict(common, name=f'iny-{fast}-{xf}-{y}', xf=xf,
                                   y=y if not xf else y&255, code=[0xC8,0x6B]))
        for value in (0, 1, 0x8000, 0xFFFF):
            result.append(dict(common, name=f'transfers-{fast}-{value}', m=0, x=value,
                               code=[0x8A,0x48,0x4A,0xA8,0x5A,0xAA,0x7A,0x68,
                                     0x8D,0,0x10,0x8E,2,0x10,0x8C,4,0x10,0x6B]))
        for y in (0, 1, 0xFF, 0xFFFF):
            address = 0x7EFFFF+y
            result.append(dict(common, name=f'ora-{fast}-{y}', m=0, y=y, db=0x7E,
                               code=[0xA9,1,0,0x19,0xFF,0xFF,0x6B],
                               memory={address:0x80,(address+1)&0xFFFFFF:0x40}))
        result.append(dict(common, name=f'ora-wrap-{fast}', m=0, db=0xFF,
                           code=[0xA9,1,0,0x19,0xFF,0xFF,0x6B],
                           memory={0xFFFFFF:0x80,0:0x40}))
        for opcode in (0xB7, 0xF7):  # LDA/SBC [dp],Y.
            for d in (0, 1, 0xFFBF):
                for y in (0, 1, 0xFFFF):
                    pointer = (d+0x40)&65535
                    memory = {pointer:0xFF,(pointer+1)&65535:0xFF,
                              (pointer+2)&65535:0x7E,0x7EFFFF+y:0x19}
                    result.append(dict(common, name=f'indirect-{fast}-{opcode}-{d}-{y}',
                                       d=d, y=y, memory=memory,
                                       code=[0xA9,0x80,0x38,opcode,0x40,0x6B]))
        result.append(dict(common, name=f'indirect-decimal-{fast}', status=9,
                           memory={0x40:0,0x41:0x10,0x42:0,0x1000:0x19},
                           code=[0xA9,0x80,0xF7,0x40,0x6B]))
        # Three iterations, each with nested Y/A saves. The selected entries
        # have depths two and six inside X/Y/A saves. Local JMP closes the loop.
        code = [0xDA,0xA0,3,0,0xC2,0x20,0x5A,0x48,0x80,0,0xA9,1,0x80,0x4A,
                0x68,0x7A,0x88,0xF0,3,0x4C,6,0x80,0xE2,0x20,0xFA,0x6B]
        for short in (False, True):
            result.append(dict(common, name=f'saved-loop-{fast}-{short}', x=0xABCD,
                               code=code[:-1]+[0x60 if short else 0x6B],
                               short_call=short,
                               continuations=[(common['pc']+6,0,0),
                                              (common['pc']+10,0,0)]))
        for compiled in (False, True):
            # $008100 is reserved by the underlying bridge fixture's fake AOT.
            target = (common['pc'] & 0xFF0000) | 0x8200
            result.append(dict(common, name=f'saved-call-{fast}-{compiled}', x=0xABCD,
                               code=code[:-1]+[0x22,0,0x82,target>>16,0x6B],
                               continuations=[(common['pc']+6,0,0)],
                               memory={target:0xEA,target+1:0x6B},
                               **{'callees' if compiled else 'interpreted_callees':[target]}))
    return result


class SavedStackContinuations(unittest.TestCase):
    def test_fail_closed(self):
        for code in ([0xC2,0x20,0x48,0xE2,0x20,0x68,0x6B],
                     [0x5A,0x6B], [0x7A,0x6B],
                     [0xC2,0x20,0xB7,0x40,0x6B],
                     [0xC2,0x20,0x6E,0x40,0,0x6B]):
            with patch.dict(os.environ, {'SNESRECOMP_EMIT_INSTRUCTION_TIMING':'008000:1:0'}):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    timing.emit_function(bytes(code)+bytes(32768-len(code)),
                                         bank=0,start=0x8000,entry_m=1,entry_x=0)

    def test_complete_and_event_parity(self):
        with tempfile.TemporaryDirectory(prefix='snes-saved-stack-') as temp, patch.dict(
                os.environ, {k:v for k,v in os.environ.items()
                             if not k.startswith('SNESRECOMP_')}, clear=True):
            tests=cases(); binary=timing.build(Path(temp),os.environ.get('CC','cc'),tests)
            def run(i,tier,*args):
                p=subprocess.run([str(binary),str(i),tier,*args],capture_output=True,
                                 text=True,timeout=10)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                return json.loads(p.stdout)
            for i,c in enumerate(tests):
                with self.subTest(case=c['name']):
                    self.assertEqual(run(i,'interp'),run(i,'aot'))
            checks=entries=0
            # Every new instruction family and each saved-stack loop variant.
            selected=[i for i,c in enumerate(tests) if
                      c['name'].startswith(('saved-', 'ora-', 'transfers-', 'indirect-decimal-'))
                      or c['name'].endswith(('-65471-65535','-1-1','-0-65535','-1-255','-0-65471'))]
            for i in selected:
                for event in ('63','100','180','300','refresh','beam-nmi','irq:120'):
                    for resume in ((),('repeat',)):
                        pair=[run(i,tier,event,*resume) for tier in ('event-interp','event-aot')]
                        entries+=pair[1].pop('continuation_entries',0)
                        pair[0].pop('continuation_entries',None)
                        with self.subTest(case=tests[i]['name'],event=event,resume=resume):
                            self.assertEqual(*pair)
                        checks+=1
            for i,c in enumerate(tests):
                if not c.get('continuations'): continue
                for event in ('63','180','refresh','irq:120'):
                    pair=[run(i,tier,event,'repeat') for tier in ('event-interp','event-continuation')]
                    entries+=pair[1].pop('continuation_entries')
                    pair[0].pop('continuation_entries')
                    with self.subTest(case=c['name'],event=event,start='interpreted'):
                        self.assertEqual(*pair)
                    checks+=1
            self.assertGreater(entries,0)
            print(f'Saved stack: {len(tests)} complete, {checks} event comparisons, {entries} native entries')


if __name__=='__main__': unittest.main()
