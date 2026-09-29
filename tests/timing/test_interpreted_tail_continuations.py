"""Known interpreted JML tails preserve local saves and scheduler ownership."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run as timing
from v2 import codegen


def cases():
    result = []
    for fast in (0, 1):
        pc = 0x808000 if fast else 0x8000
        for cross in (False, True):
            target = ((pc >> 16) ^ (0x80 if cross else 0)) << 16 | 0x8200
            jml = [0x5C,target & 255,(target >> 8) & 255,target >> 16]
            for short in (False, True):
                ret = 0x60 if short else 0x6B
                common = dict(pc=pc, memsel=fast, x=0xABCD, y=0x5678,
                              instruction_timing=True, status=0, short_call=short,
                              interpreted_callees=[target])
                # Two bytes remain saved across each interpreted tail. The
                # interpreted target jumps back to the exact internal header.
                code = [0xDA,0xA2,3,0,0x80,0,0xCA,0xF0,4]+jml+[0xFA,ret]
                helper = [0xEA,0x5C,6,0x80,pc >> 16]
                result.append(dict(common, name=f'loop-{fast}-{cross}-{short}',
                                   code=code, continuations=[(pc+6,1,0)],
                                   memory={target+i:b for i,b in enumerate(helper)}))
                # The interpreted suffix restores all six local bytes and
                # returns through the original frame. It changes M on exit.
                code = [0xDA,0x5A,0xC2,0x20,0x48,0xA2,3,0,0xCA,0xD0,0xFD]+jml
                helper = [0x68,0xE2,0x20,0x7A,0xFA,ret]
                result.append(dict(common, name=f'suffix-{fast}-{cross}-{short}',
                                   code=code, continuations=[(pc+8,0,0)],
                                   memory={target+i:b for i,b in enumerate(helper)}))
                # A tail with zero local depth uses the same owner handoff.
                code = [0xA2,3,0,0xCA,0xD0,0xFD]+jml
                helper = [0xEA,ret]
                result.append(dict(common, name=f'balanced-{fast}-{cross}-{short}',
                                   code=code, continuations=[(pc+3,1,0)],
                                   memory={target+i:b for i,b in enumerate(helper)}))
    return result


class InterpretedTailContinuations(unittest.TestCase):
    def test_fail_closed(self):
        # A name alone is not proof that the exact target stays interpreted.
        # Local saves at calls/returns, mismatched joins and over-pulls still fail.
        tests = [
            ([0xDA,0xA2,3,0,0xCA,0xD0,0xFD,0x5C,0,0x82,0],4,False),
            ([0xDA,0xA2,3,0,0xCA,0xD0,0xFD,0x22,0,0x82,0,0xFA,0x6B],4,True),
            ([0xDA,0xA2,3,0,0xCA,0xD0,0xFD,0x6B],4,True),
            ([0xA2,3,0,0xDA,0xCA,0xD0,0xFC,0x5C,0,0x82,0],3,True),
            ([0xA2,3,0,0xFA,0xCA,0xD0,0xFC,0x5C,0,0x82,0],3,True),
        ]
        for code, point, interpreted in tests:
            with patch.dict(os.environ, {
                    'SNESRECOMP_EMIT_INSTRUCTION_TIMING':'008000:1:0',
                    'SNESRECOMP_EMIT_CONTINUATIONS':f'008000:1:0>{0x8000+point:06X}:1:0'}):
                codegen.set_name_resolver({0x8200:'bank_00_8200'})
                codegen.set_valid_variants({0x8200:frozenset() if interpreted else {(1,0)}},
                                          authoritative=True)
                try:
                    with self.subTest(code=code), self.assertRaises(ValueError):
                        timing.emit_function(bytes(code)+bytes(32768-len(code)),
                                             bank=0,start=0x8000,entry_m=1,entry_x=0)
                finally:
                    codegen.set_name_resolver({})
                    codegen.set_valid_variants({})

    def test_complete_and_event_parity(self):
        clean = {k:v for k,v in os.environ.items() if not k.startswith('SNESRECOMP_')}
        with tempfile.TemporaryDirectory(prefix='snes-tail-resume-') as temp, patch.dict(
                os.environ, clean, clear=True):
            tests = cases()
            binary = timing.build(Path(temp),os.environ.get('CC','cc'),tests)

            def run(i,tier,*args):
                p = subprocess.run([str(binary),str(i),tier,*args],capture_output=True,
                                   text=True,timeout=10)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                return json.loads(p.stdout)

            checks = entries = 0
            for i, case in enumerate(tests):
                with self.subTest(case=case['name']):
                    self.assertEqual(run(i,'interp'),run(i,'aot'))
                for event in ('63','100','150','200','260','refresh','beam-nmi','irq:120'):
                    for resume in ((),('repeat',)):
                        pair = [run(i,tier,event,*resume) for tier in ('event-interp','event-aot')]
                        pair[0].pop('continuation_entries')
                        entries += pair[1].pop('continuation_entries')
                        with self.subTest(case=case['name'],event=event,resume=resume):
                            self.assertEqual(*pair)
                        checks += 1
                    pair = [run(i,tier,event,'repeat')
                            for tier in ('event-interp','event-continuation')]
                    pair[0].pop('continuation_entries')
                    entries += pair[1].pop('continuation_entries')
                    with self.subTest(case=case['name'],event=event,start='interpreted'):
                        self.assertEqual(*pair)
                    checks += 1
            self.assertGreater(entries,0)
            print(f'Interpreted tails: {len(tests)} complete, {checks} event comparisons, {entries} native entries')


if __name__ == '__main__':
    unittest.main()
