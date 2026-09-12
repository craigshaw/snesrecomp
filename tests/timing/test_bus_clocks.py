"""Executable final-clock checks. Write/event timestamp parity is separate."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import run as timing
from test_branch_cycles import branch_cases


def memory_cases():
    # mode, LDA, STA, read base, store base, operand length, pointer bytes.
    modes = [
        ("dp", 0xA5, 0x85, 3, 3, 1, 0),
        ("dpx", 0xB5, 0x95, 4, 4, 1, 0),
        ("abs", 0xAD, 0x8D, 4, 4, 2, 0),
        ("absx", 0xBD, 0x9D, 4, 5, 2, 0),
        ("absy", 0xB9, 0x99, 4, 5, 2, 0),
        ("long", 0xAF, 0x8F, 5, 5, 3, 0),
        ("longx", 0xBF, 0x9F, 5, 5, 3, 0),
        ("dpi", 0xB2, 0x92, 5, 5, 1, 2),
        ("dpix", 0xA1, 0x81, 6, 6, 1, 2),
        ("dpiy", 0xB1, 0x91, 5, 6, 1, 2),
        ("dpl", 0xA7, 0x87, 6, 6, 1, 3),
        ("dply", 0xB7, 0x97, 6, 6, 1, 3),
        ("sriy", 0xB3, 0x93, 7, 7, 1, 2),
    ]
    cases = []
    for mode, read, write, rc, wc, operands, ptrbytes in modes:
        for store in (False, True):
            for m in (0, 1):
                for target, speed in ((0x1020, 8), (0x4220, 6), (0x4020, 12)):
                    width = 2 - m
                    case = dict(name=f"{mode}-{'store' if store else 'read'}-M{m}-{target:04X}",
                                m=m, x=1, y=1)
                    if ptrbytes:
                        operand = 0x40 if mode != 'sriy' else 8
                        pointer = (0x1FC + operand) if mode == 'sriy' else operand + (mode == 'dpix')
                        base = target - (mode in ('dpiy', 'dply', 'sriy'))
                        case['memory'] = {pointer + i: (base >> (8*i)) & 255 for i in range(ptrbytes)}
                    elif mode.startswith('dp'):
                        operand = 0x20 - (mode == 'dpx')
                        case['d'] = target & 0xFF00
                    else:
                        operand = target - (mode in ('absx', 'absy', 'longx'))
                    case['code'] = [write if store else read] + [
                        (operand >> (8*i)) & 255 for i in range(operands)] + [0x6B]
                    cpu = (wc if store else rc) + (1-m)
                    bus = 1 + operands + ptrbytes + width
                    case['cpu'] = cpu + 6
                    case['master'] = (1+operands)*8 + ptrbytes*8 + width*speed + (cpu-bus)*6 + 44
                    cases.append(case)
    return cases


def indexed_write_cases():
    cases = []
    # The indexed internal cycle is already in these base costs. Index width
    # and page crossing must not add it again. WDC W65C816S datasheet,
    # Tables 3-1 and 5-4; native mode, with accumulator width applied below.
    for op, base, rmw in ((0x9D, 5, False), (0x99, 5, False),
                          (0x9E, 5, False), (0x91, 6, False),
                          *((op, 7, True) for op in (0x1E, 0x3E, 0x5E, 0x7E, 0xDE, 0xFE))):
        for m in (0, 1):
            for xf in (0, 1):
                for cross in (False, True):
                    addr = 0x10FF if cross else 0x1020
                    case = dict(name=f"indexed-{op:02X}-M{m}X{xf}-cross{int(cross)}",
                                m=m, xf=xf, x=1, y=1)
                    if op == 0x91:
                        case['code'] = [op, 0x40, 0x6B]
                        case['memory'] = {0x40: addr & 255, 0x41: addr >> 8}
                        fetch, ptr = 2, 2
                    else:
                        case['code'] = [op, addr & 255, addr >> 8, 0x6B]
                        fetch, ptr = 3, 0
                    cpu = base + (2 if rmw else 1)*(1-m)
                    data = (2-m)*(2 if rmw else 1)
                    case['cpu'] = cpu + 6
                    case['master'] = (fetch+ptr+data)*8 + (cpu-fetch-ptr-data)*6 + 44
                    cases.append(case)
    return cases


def stack_and_move_cases():
    cases = [
        dict(name='pha-pla-8', code=[0x48, 0x68, 0x6B], cpu=13, master=94),
        dict(name='pha-pla-16', code=[0x48, 0x68, 0x6B], m=0, cpu=15, master=110),
        dict(name='phx-plx-8', code=[0xDA, 0xFA, 0x6B], xf=1, cpu=13, master=94),
        dict(name='phx-plx-16', code=[0xDA, 0xFA, 0x6B], cpu=15, master=110),
        dict(name='phy-ply-16', code=[0x5A, 0x7A, 0x6B], cpu=15, master=110),
        dict(name='php-plp', code=[0x08, 0x28, 0x6B], cpu=13, master=94),
        dict(name='phb-plb', code=[0x8B, 0xAB, 0x6B], cpu=13, master=94),
        dict(name='phd-pld', code=[0x0B, 0x2B, 0x6B], cpu=15, master=110),
        dict(name='pea-pla', code=[0xF4, 0x34, 0x12, 0x68, 0x6B], m=0, cpu=16, master=120),
        dict(name='per-pla', code=[0x62, 0x34, 0x12, 0x68, 0x6B], m=0, cpu=17, master=126),
        dict(name='pei-pla', code=[0xD4, 0x40, 0x68, 0x6B], m=0, cpu=17, master=128),
        dict(name='stack-relative-8', code=[0xA3, 8, 0x6B], cpu=10, master=74),
        dict(name='stack-relative-16', code=[0xA3, 8, 0x6B], m=0, cpu=11, master=82),
        dict(name='rti', code=[0x40], cpu=7, master=52),
        dict(name='jsl-nop-rtl', code=[0x22, 0x10, 0x80, 0, 0x6B],
             memory={0x8010:0xEA, 0x8011:0x6B}, callees=[0x8010], cpu=22, master=164),
        dict(name='jsr-nop-rts', code=[0x20, 0x20, 0x80, 0x6B],
             memory={0x8020:0xEA, 0x8021:0x60}, callees=[0x8020], cpu=20, master=146),
    ]
    for op in (0x54, 0x44):
        for count in (1, 3):
            cases.append(dict(name=f'move-{op:02X}-{count}', m=0,
                              code=[0xA9, count-1, 0, op, 0x7E, 0x40, 0x6B],
                              cpu=9+count*7, master=68+count*52))
    return cases


def boundary_cases():
    cases = []
    for addr, clocks in ((0x1FFF, 14), (0x3FFF, 18), (0x41FF, 18), (0x5FFF, 14)):
        for op in (0xAD, 0x8D):
            cases.append(dict(name=f'word-boundary-{op:02X}-{addr:04X}', m=0,
                              code=[op, addr & 255, addr >> 8, 0x6B],
                              cpu=11, master=24+clocks+44))
    for m in (0, 1):
        cases.append(dict(name=f'dp-low-penalty-M{m}', m=m, d=1,
                          code=[0xA5, 0x40, 0x6B], cpu=11-m, master=82-m*8))
    return cases


class BusClocks(unittest.TestCase):
    def test_final_clocks_and_architecture(self):
        cases = (timing.CASES + memory_cases() + indexed_write_cases()
                 + stack_and_move_cases() + boundary_cases())
        # Native conditional branch cycles beyond the instruction bytes are
        # internal. RTL reads its low-WRAM stack at eight clocks in both ROM modes.
        for case in branch_cases():
            # Derive executed fetch count from CPU total instead: load is
            # entirely fetches, branch has two fetches and possibly one
            # internal cycle, NOP/RTL contribute 1 and 2 internal cycles.
            branch_index = 2 if (case['m'] if case['code'][0] == 0xA9 else case['xf']) else 3
            value = int.from_bytes(bytes(case['code'][1:branch_index]), 'little')
            taken = (value != 0) if case['code'][branch_index] == 0xD0 else (value == 0)
            nop = case['code'][branch_index+1] == 1 and not taken
            fetch = branch_index + 2 + int(nop) + 1
            case['master'] = fetch*(6 if case['memsel'] else 8) + 3*8 + (int(taken)+int(nop)+2)*6
            cases.append(case)
        old = os.environ.get('SNESRECOMP_EMIT_BUS_TIMING')
        os.environ['SNESRECOMP_EMIT_BUS_TIMING'] = '1'
        try:
            with tempfile.TemporaryDirectory(prefix='snesrecomp-bus-clocks-') as tmp:
                out = Path(tmp)
                binary = timing.build(out, os.environ.get('CC', 'cc'), cases)
                write_order_differences = []
                for index, case in enumerate(cases):
                    with self.subTest(case=case['name']):
                        states = []
                        for tier in ('interp', 'aot'):
                            result = subprocess.run([str(binary), str(index), tier], cwd=out,
                                                    text=True, capture_output=True, timeout=10)
                            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                            state = json.loads(result.stdout)
                            self.assertEqual(state['cpu_cycles'], case['cpu'], (tier, state))
                            self.assertEqual(state['master_cycles'], case['master'], (tier, state))
                            for write in state['writes']:
                                write.pop('master')  # Explicitly outside this test's scope.
                            states.append(state)
                        if states[0]['writes'] != states[1]['writes']:
                            write_order_differences.append(case['name'])
                        # Final memory is in scope here. Preserve the separate
                        # write-order finding for the later write/event work.
                        for state in states:
                            state['writes'] = {w['address']: w['value'] for w in state['writes']}
                        self.assertEqual(*states)
                print(f"Final clocks/state checked in {len(cases)} cases; "
                      f"write order differs in {len(write_order_differences)} cases.")
        finally:
            if old is None:
                os.environ.pop('SNESRECOMP_EMIT_BUS_TIMING', None)
            else:
                os.environ['SNESRECOMP_EMIT_BUS_TIMING'] = old


if __name__ == '__main__':
    for key in tuple(os.environ):
        if key.startswith('SNESRECOMP_'):
            os.environ.pop(key)
    unittest.main()
