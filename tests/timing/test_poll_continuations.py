"""Instruction-timed memory polls, terminal status restores and word data reads."""
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
        pc = 0x808000 if fast else 0x8000
        common = dict(pc=pc, memsel=fast, instruction_timing=True, db=0x42)
        for status in (0, 1, 0xC8):
            # Actual waits, one saved status byte, and both return sizes.
            for word in (False, True):
                prefix = [0x08,0xC2,0x30,0xA9,1,0] if word else [0x08,0xA9,1]
                point = pc + len(prefix)
                for short in (False, True):
                    code = prefix + [0xCD,0x40,0x21,0xD0,0xFB,0x28,0x60 if short else 0x6B]
                    result.append(dict(common, name=f'poll-{fast}-{status}-{word}-{short}',
                        code=code, db=0, status=status, short_call=short,
                        poll_address=0x2140, poll_ready=350,
                        continuations=[(point,int(not word),0)]))
            # Preserve A.high while ROL changes all low-byte carry/N/Z cases.
            for value in (0,0x7F,0x80,0xFF):
                result.append(dict(common, name=f'rol-{fast}-{status}-{value}', status=status,
                    code=[0xC2,0x20,0xA9,value,0xAB,0xE2,0x20,0x2A,0x6B]))
        for xf in (0, 1):
            prefix = [0x08,0xC2,0x20 if xf else 0x30,0xA2,3] + ([] if xf else [0])
            result.append(dict(common, name=f'status-{fast}-{xf}', xf=xf, status=0x49,
                code=prefix+[0xCA,0xD0,0xFD,0xE2,0x30 if xf else 0x20,0x28,0x6B],
                continuations=[(pc+len(prefix),0,xf)]))
        # Data words carry banks. Pointer reads wrap in bank zero. Distinct
        # sentinels catch either wrap error, plus indexed 24-bit overflow.
        for pointer, base, y in ((0x20,0x40FFFF,0),(0xFFFF,0x40FFFE,1),
                                 (0x20,0xFFFFFF,0),(0x20,0xFFFFFE,2)):
            effective=(base+y)&0xFFFFFF
            memory={pointer:base&255,(pointer+1)&65535:(base>>8)&255,
                    (pointer+2)&65535:base>>16,
                    effective:0x34,(effective+1)&0xFFFFFF:0xA7}
            result.append(dict(common, name=f'long-word-{fast}-{pointer}-{base}-{y}',
                d=(pointer-0x20)&65535, y=y, memory=memory,
                code=[0x08,0xC2,0x30,0xB7,0x20,0x28,0x6B]))
    return result


class PollContinuations(unittest.TestCase):
    def test_fail_closed(self):
        # Actual X changes, nonterminal status restores and caller-frame pulls
        # remain rejected. The memory-poll guard stays in ordinary generation.
        for code, xf in (([0xC2,0x10,0x6B],1),([0xE2,0x10,0x6B],0),
                         ([0x08,0x28,0xEA,0x6B],0),([0x28,0x6B],0),
                         ([0x08,0x6B],0)):
            with patch.dict(os.environ, {'SNESRECOMP_EMIT_INSTRUCTION_TIMING':f'008000:1:{xf}'}):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    timing.emit_function(bytes(code)+bytes(32768-len(code)),
                                         bank=0,start=0x8000,entry_m=1,entry_x=xf)
        rom=bytes([0xCD,0x40,0x21,0xD0,0xFB,0x6B])+bytes(32762)
        with patch.dict(os.environ, {'SNESRECOMP_EMIT_INSTRUCTION_TIMING':''}):
            text=timing.emit_function(rom,bank=0,start=0x8000,entry_m=1,entry_x=0)
            self.assertIn('if (interp_bridge_in_lle_scheduler())',text)

    def test_complete_and_events(self):
        clean={k:v for k,v in os.environ.items() if not k.startswith('SNESRECOMP_')}
        with tempfile.TemporaryDirectory(prefix='snes-poll-resume-') as temp, patch.dict(
                os.environ,clean,clear=True):
            tests=cases()
            binary=timing.build(Path(temp),os.environ.get('CC','cc'),tests)
            def run(i,tier,*args):
                p=subprocess.run([str(binary),str(i),tier,*args],capture_output=True,
                                 text=True,timeout=10)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                return json.loads(p.stdout)
            checks=entries=0
            for i,case in enumerate(tests):
                with self.subTest(case=case['name']):
                    a,b=run(i,'interp'),run(i,'aot')
                    self.assertEqual(a,b)
                    if case['name'].startswith('long-word'):
                        self.assertEqual(a['a'],0xA734)
                for event in ('63','100','180','300','refresh','beam-nmi','irq:120'):
                    for resume in ((),('repeat',)):
                        pair=[run(i,tier,event,*resume) for tier in ('event-interp','event-aot')]
                        pair[0].pop('continuation_entries',None)
                        entries+=pair[1].pop('continuation_entries',0)
                        with self.subTest(case=case['name'],event=event,resume=resume):
                            self.assertEqual(*pair)
                        checks+=1
                    if case.get('continuations'):
                        pair=[run(i,tier,event,'repeat') for tier in ('event-interp','event-continuation')]
                        pair[0].pop('continuation_entries')
                        entries+=pair[1].pop('continuation_entries')
                        with self.subTest(case=case['name'],event=event,start='interpreted'):
                            self.assertEqual(*pair)
                        checks+=1
            self.assertGreater(entries,0)
            print(f'Poll/status: {len(tests)} complete, {checks} event comparisons, {entries} native entries')


if __name__ == '__main__':
    unittest.main()
