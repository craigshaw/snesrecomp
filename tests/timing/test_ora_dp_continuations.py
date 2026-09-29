"""Direct-page ORA timing and saved-stack loop continuation parity."""
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
        common = dict(instruction_timing=True, xf=0,
                      pc=0x808000 if fast else 0x8000, memsel=fast)
        for m in (0, 1):
            for d in (0, 1, 0xFFBF):
                for value in (0, 0x80, 0x8000, 0xFFFF):
                    address = (d + 0x40) & 65535
                    # Start with a word load to check the hidden high byte
                    # across SEP and byte ORA. ORA must preserve C, V and D.
                    for status in (0, 0x49):
                        code = [0xA9, 0, 0x21] + ([0xE2, 0x20] if m else [])
                        result.append(dict(common, name=f'ora-{fast}-{m}-{d}-{value}-{status}',
                                           m=0, d=d, status=status,
                                           code=code+[0x05, 0x40, 0x6B],
                                           memory={address:value & 255,
                                                   (address+1)&65535:value >> 8}))
        # The loop header follows PHX at depth two. Each iteration uses
        # both ORA widths, so event recovery also crosses an M transition.
        code = [0xDA,0xA0,3,0,0x80,0,0xA9,1,0x05,0x40,0xC2,0x20,
                0xA9,0,1,0x05,0x40,0xE2,0x20,0x88,0xD0,0xF0,0xFA]
        for short in (False, True):
            result.append(dict(common, name=f'loop-{fast}-{short}', m=1, x=0xABCD,
                               d=0xFFBF, status=0x49, short_call=short,
                               code=code+[0x60 if short else 0x6B],
                               memory={0xFFFF:0x80,0:0x40},
                               continuations=[(common['pc']+6,1,0)]))
    return result


class OraDirectPageContinuations(unittest.TestCase):
    def test_complete_and_event_parity(self):
        clean = {k:v for k,v in os.environ.items() if not k.startswith('SNESRECOMP_')}
        with tempfile.TemporaryDirectory(prefix='snes-ora-dp-') as temp, patch.dict(
                os.environ, clean, clear=True):
            tests = cases()
            binary = timing.build(Path(temp), os.environ.get('CC','cc'), tests)

            def run(i, tier, *args):
                result = subprocess.run([str(binary),str(i),tier,*args],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                return json.loads(result.stdout)

            for i, case in enumerate(tests):
                with self.subTest(case=case['name']):
                    self.assertEqual(run(i,'interp'), run(i,'aot'))
            checks = entries = 0
            for i, case in enumerate(tests):
                if not (case.get('continuations') or case['name'].endswith('-32768-73')):
                    continue
                for event in ('63','100','180','300','refresh','beam-nmi','irq:120'):
                    for resume in ((), ('repeat',)):
                        pair = [run(i,tier,event,*resume)
                                for tier in ('event-interp','event-aot')]
                        pair[0].pop('continuation_entries',None)
                        entries += pair[1].pop('continuation_entries',0)
                        with self.subTest(case=case['name'],event=event,resume=resume):
                            self.assertEqual(*pair)
                        checks += 1
                if case.get('continuations'):
                    for event in ('63','180','refresh','irq:120'):
                        pair = [run(i,tier,event,'repeat')
                                for tier in ('event-interp','event-continuation')]
                        pair[0].pop('continuation_entries')
                        entries += pair[1].pop('continuation_entries')
                        with self.subTest(case=case['name'],event=event,start='interpreted'):
                            self.assertEqual(*pair)
                        checks += 1
            self.assertGreater(entries, 0)
            print(f'ORA dp: {len(tests)} complete, {checks} event comparisons, {entries} native entries')


if __name__ == '__main__':
    unittest.main()
