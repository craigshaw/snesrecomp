"""Axis-2 step C: the v2 emitter charges each block's static 65816 CPU
cycles as a per-block constant (recompiler/snes_cycles.py via
emit_function._block_cycle_const). Guards the cost-model -> emitter wiring."""
import json
import os
import pathlib
import re
import tempfile
from unittest.mock import patch

from _helpers import make_lorom_bank0  # noqa: E402
from v2.emit_function import emit_function  # noqa: E402


# A standalone per-block static charge: `    cpu->cycles += N;` on its own
# line (the dynamic charges are `if (...) cpu->cycles += 1; /* ... */`).
_STATIC_CHARGE = re.compile(r'^\s*cpu->cycles \+= (\d+);\s*$', re.M)


def test_selected_bus_costs_match_global_and_do_not_leak():
    # Indexed read, MMIO write and return cover nested codegen timing helpers.
    rom = make_lorom_bank0({0x8000: bytes([0xBD, 0x34, 0x12, 0x8D, 0, 0x42, 0x6B])})
    from v2 import bus_timing
    with patch.dict(os.environ, {}, clear=True):
        ordinary = emit_function(rom, 0, 0x8000, 1, 0)
        os.environ[bus_timing.BUS_TARGETS_ENV] = '008000:1:0'
        selected = emit_function(rom=rom, bank=0, start=0x8000, entry_m=1, entry_x=0)
        assert not bus_timing.enabled()
        assert selected != ordinary
        assert 'CpuAotInstructionTiming _aot_timing;' not in selected
        for key in ('008000:0:0', '008000:1:1', '018000:1:0', '008001:1:0'):
            os.environ[bus_timing.BUS_TARGETS_ENV] = key
            assert emit_function(rom, 0, 0x8000, 1, 0) == ordinary
        os.environ['SNESRECOMP_EMIT_BUS_TIMING'] = '1'
        assert emit_function(rom, 0, 0x8000, 1, 0) == selected


def test_selected_bus_scope_is_restored_after_emission_error():
    from v2 import bus_timing
    rom = make_lorom_bank0({0x8000: bytes([0xEA, 0x6B])})
    with patch.dict(os.environ, {bus_timing.BUS_TARGETS_ENV: '008000:1:0'}, clear=True):
        with patch('v2.emit_function.decode_function', side_effect=RuntimeError('decode failed')):
            try:
                emit_function(rom, 0, 0x8000, 1, 0)
            except RuntimeError as exc:
                assert str(exc) == 'decode failed'
            else:
                assert False, 'expected decode error'
        assert not bus_timing.enabled()


def test_linear_block_charges_static_cycles():
    # LDA #$05 (2) ; STA $00 (dp, 3) ; RTS (6) -> one block, 11 static cycles
    # (plus a runtime D.l!=0 dynamic charge for the dp store).
    rom = make_lorom_bank0({0x8000: bytes([0xA9, 0x05, 0x85, 0x00, 0x60])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=1)
    charges = _STATIC_CHARGE.findall(src)
    assert charges == ['11'], f'expected one static block charge of 11, got {charges}'
    # dp dynamic present (Axis-5 reworded it to also charge master clocks).
    assert "if (cpu->D & 0xFF) { cpu->cycles += 1;" in src
    # Axis-5: the static block charge is region-weighted into master_cycles.
    # Bank 0 LoROM = SLOW (8 master/CPU cycle) -> 11 * 8 = 88.
    assert "cpu->master_cycles += 88;" in src, src
    assert src.index(
        "cpu->coprocessor_master_cycles = cpu->master_cycles;"
    ) < src.index("cpu->master_cycles += 88;"), src


def test_width_widens_static_charge():
    # 16-bit (REP #$30) LDA #$1234 (3) ; RTS (6) -> 9. The native REP itself
    # (CLC/XCE/REP) live in the entry block; assert the 16-bit LDA path adds
    # the m=0 cycle (base 2 + 1). We check the total contains a charge whose
    # value reflects 16-bit accounting (>= the 8-bit equivalent).
    rom8 = make_lorom_bank0({0x8000: bytes([0xA9, 0x05, 0x60])})            # LDA# 8b ; RTS
    rom16 = make_lorom_bank0({0x8000: bytes([0xC2, 0x20, 0xA9, 0x34, 0x12, 0x60])})  # REP#$20; LDA#16b; RTS
    s8 = emit_function(rom8, bank=0, start=0x8000, entry_m=1, entry_x=1)
    s16 = emit_function(rom16, bank=0, start=0x8000, entry_m=1, entry_x=1)
    c8 = sum(int(x) for x in re.findall(r'cpu->cycles \+= (\d+);', s8))
    c16 = sum(int(x) for x in re.findall(r'cpu->cycles \+= (\d+);', s16))
    # 8b: LDA# 2 + RTS 6 = 8. 16b: REP 3 + LDA#(2+1) + RTS 6 = 12.
    assert c8 == 8, f'8-bit total {c8} != 8'
    assert c16 == 12, f'16-bit total {c16} != 12 (m=0 LDA should add 1)'


def test_dp_dynamic_charge_emitted():
    # LDA $00 (DP mode) ; RTS — the D.l!=0 charge is runtime-conditional.
    rom = make_lorom_bank0({0x8000: bytes([0xA5, 0x00, 0x60])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=1)
    # The runtime D.l!=0 charge bumps both cycles and the region-weighted master.
    assert "if (cpu->D & 0xFF) { cpu->cycles += 1; cpu->master_cycles += 8; }" in src, src


def test_abs_x_page_cross_dynamic_charge_emitted():
    # LDA $1234,X (read) ; RTS — page-cross charge uses the static base $1234.
    rom = make_lorom_bank0({0x8000: bytes([0xBD, 0x34, 0x12, 0x60])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=1)
    assert "0x1234 & 0xFF00" in src and "+ cpu->X) & 0xFF00)" in src, src
    assert "/* abs,X read page-cross */" in src


def test_taken_branch_charges_one_cycle():
    # BNE fork — the taken edge must add +1 cycle (block const = not-taken base).
    rom = make_lorom_bank0({0x8000: bytes([
        0xD0, 0x02,  # BNE $8004
        0xEA, 0x60,  # NOP; RTS
        0xEA, 0x60,  # NOP; RTS (taken)
    ])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=1)
    # Taken edge: +1 CPU cycle plus its region-weighted master charge, then goto.
    assert re.search(
        r'if \(.*\) \{ cpu->cycles \+= 1; cpu->master_cycles \+= \d+; goto ', src), src


def test_store_abs_x_has_no_page_cross_charge():
    # STA $1234,X (store) — stores pay a fixed cost (in the base), no cross add.
    rom = make_lorom_bank0({0x8000: bytes([0x9D, 0x34, 0x12, 0x60])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=1)
    assert "page-cross" not in src, src


def test_folded_branch_charges_only_the_taken_edge():
    for branch in (0xD0, 0xF0):  # BNE, BEQ
        for value in (0, 1):
            rom = make_lorom_bank0({0x8000: bytes([
                0xA9, value, branch, 0x01, 0xEA, 0x6B,
            ])})
            src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=0)
            taken = (value != 0) if branch == 0xD0 else (value == 0)
            charge = "cpu->cycles += 1; cpu->master_cycles += 8;  /* folded taken branch */"
            assert src.count(charge) == int(taken), src


def test_folded_taken_branch_audit_uses_branch_pc():
    rom = make_lorom_bank0({0x8000: bytes([0xA9, 1, 0xD0, 1, 0xEA, 0x6B])})
    name = 'SNESRECOMP_EMIT_EVENT_CROSSING_AUDIT'
    previous = os.environ.get(name)
    try:
        os.environ[name] = '1'
        src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=0)
        assert (
            "interp_bridge_event_audit_charge(cpu, 0x008002u, 8); "
            "cpu->cycles += 1; cpu->master_cycles += 8;  /* folded taken branch */"
        ) in src, src
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def test_bus_timing_separates_base_fetch_and_data_charges():
    rom = make_lorom_bank0({0x8000: bytes([0xA9, 0, 0x8D, 0, 0x42, 0x6B])})
    with patch.dict(os.environ, {'SNESRECOMP_EMIT_BUS_TIMING': '1',
                                'SNESRECOMP_EMIT_EVENT_CROSSING_AUDIT': '1'}):
        src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=0)
    assert 'cpu->master_cycles += 72;' in src  # 12 CPU cycles at six clocks.
    assert 'cpu_aot_fetch_extra(cpu, 0x008000u, 2, 1);' in src
    assert 'cpu_aot_fetch_extra(cpu, 0x008002u, 3, 1);' in src
    assert 'cpu_aot_write8(cpu, 0x008002u, 1,' in src
    # Host return-frame inspection is not a guest bus transfer.
    prologue = src[:src.index('  L_8000_M1X0:')]
    assert 'cpu_read8(cpu,' in prologue
    assert 'cpu_aot_read8(cpu,' not in prologue


def test_indexed_rmw_has_no_dynamic_page_cross_charge():
    rom = make_lorom_bank0({0x8000: bytes([0xFE, 0xFF, 0x10, 0x6B])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=0)
    assert 'page-cross' not in src


def test_every_block_with_insns_is_charged():
    # BCS fork -> three blocks (entry, fall-through, taken), each non-empty,
    # so each must carry a cpu->cycles charge.
    rom = make_lorom_bank0({0x8000: bytes([
        0xB0, 0x02,  # BCS $8004
        0xEA, 0x60,  # NOP; RTS (fall-through)
        0xEA, 0x60,  # NOP; RTS (taken)
    ])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=1)
    charges = re.findall(r'cpu->cycles \+= (\d+);', src)
    assert len(charges) >= 3, f'expected a charge per block (>=3), got {charges}'


def test_master_cycles_region_weighted_static_charge():
    # Axis-5 off-cue: each static block charge gets a paired master-clock charge
    # equal to (CPU cycles x code-region speed). Bank 0 ($00:$8000-$FFFF) is
    # LoROM SLOW = 8 master clocks per CPU cycle, memsel-independent.
    # LDA #$05 (2) ; RTS (6) = 8 CPU cycles -> 8 * 8 = 64 master clocks.
    rom = make_lorom_bank0({0x8000: bytes([0xA9, 0x05, 0x60])})
    src = emit_function(rom, bank=0, start=0x8000, entry_m=1, entry_x=1)
    assert "cpu->cycles += 8;" in src, src
    assert "cpu->master_cycles += 64;" in src, src
    # Every static cpu->cycles charge has exactly one master partner (no orphan).
    cyc = re.findall(r'^\s*cpu->cycles \+= (\d+);\s*$', src, re.M)
    mas = re.findall(r'^\s*cpu->master_cycles \+= (\d+);\s*$', src, re.M)
    assert len(cyc) == len(mas), f'static charge pairing mismatch: {cyc} vs {mas}'
    # And the weighting holds term-by-term (slow region => master == 8*cpu).
    for c, m in zip(cyc, mas):
        assert int(m) == int(c) * 8, f'master {m} != 8*{c}'


def test_event_crossing_audit_is_regeneration_opt_in():
    # A normal regeneration has no audit calls. An explicit audit regeneration
    # observes the static block charge and the runtime D.l penalty separately.
    rom = make_lorom_bank0({0x8000: bytes([0xA5, 0x00, 0x60])})
    name = 'SNESRECOMP_EMIT_EVENT_CROSSING_AUDIT'
    previous = os.environ.pop(name, None)
    try:
        normal = emit_function(rom, bank=0, start=0x8000,
                               entry_m=1, entry_x=1)
        assert "interp_bridge_event_audit_charge" not in normal

        os.environ[name] = '1'
        audited = emit_function(rom, bank=0, start=0x8000,
                                entry_m=1, entry_x=1)
        call = "interp_bridge_event_audit_charge(cpu, 0x008000u,"
        assert audited.count(call) == 2, audited
        assert audited.index(call) < audited.index("cpu->master_cycles += 72;")
    finally:
        os.environ.pop(name, None)
        if previous is not None:
            os.environ[name] = previous


def _event_precision_report(path, entries, *, overflow=0):
    pathlib.Path(path).write_text(json.dumps({
        "schema": "snesrecomp event crossing audit v1",
        "overflow": overflow,
        "entries": entries,
    }), encoding="utf-8")


def test_event_precision_profile_routes_only_the_observed_mx_function_to_lle():
    # An observation at a middle instruction must route from the containing
    # function's architectural entry, before any earlier aggregate block charge.
    rom = make_lorom_bank0({0x8000: bytes([0xEA, 0x0A, 0x60])})
    name = 'SNESRECOMP_EVENT_PRECISION_PROFILE'
    previous = os.environ.pop(name, None)
    try:
        normal = emit_function(rom, bank=0, start=0x8000,
                               entry_m=1, entry_x=1)
        assert "event-precision function" not in normal

        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / 'crossings.json'
            _event_precision_report(report, [{
                "pc24": 0x008001,
                "m": 1,
                "x": 1,
                "event": "nmi",
                "hits": 1,
                "irq_i_set_hits": 0,
            }])
            os.environ[name] = str(report)
            precise = emit_function(rom, bank=0, start=0x8000,
                                    entry_m=1, entry_x=1)
            assert "event-precision function" in precise
            assert "interp_bridge_lle_yield_unwind(cpu, 0x008000u)" in precise

            wrong_mx = emit_function(rom, bank=0, start=0x8000,
                                     entry_m=0, entry_x=1)
            assert "event-precision function" not in wrong_mx
    finally:
        os.environ.pop(name, None)
        if previous is not None:
            os.environ[name] = previous


def test_event_precision_profile_ignores_fully_masked_irq_crossing():
    rom = make_lorom_bank0({0x8000: bytes([0xEA, 0x60])})
    name = 'SNESRECOMP_EVENT_PRECISION_PROFILE'
    previous = os.environ.pop(name, None)
    try:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / 'crossings.json'
            _event_precision_report(report, [{
                "pc24": 0x008000,
                "m": 1,
                "x": 1,
                "event": "irq",
                "hits": 2,
                "irq_i_set_hits": 2,
            }])
            os.environ[name] = str(report)
            src = emit_function(rom, bank=0, start=0x8000,
                                entry_m=1, entry_x=1)
            assert "event-precision function" not in src
    finally:
        os.environ.pop(name, None)
        if previous is not None:
            os.environ[name] = previous


def test_event_precision_profile_rejects_incomplete_audit():
    rom = make_lorom_bank0({0x8000: bytes([0x60])})
    name = 'SNESRECOMP_EVENT_PRECISION_PROFILE'
    previous = os.environ.pop(name, None)
    try:
        with tempfile.TemporaryDirectory() as raw:
            report = pathlib.Path(raw) / 'crossings.json'
            _event_precision_report(report, [], overflow=1)
            os.environ[name] = str(report)
            try:
                emit_function(rom, bank=0, start=0x8000,
                              entry_m=1, entry_x=1)
                assert False, "overflowed audit must be rejected"
            except ValueError as exc:
                assert "incomplete" in str(exc)
    finally:
        os.environ.pop(name, None)
        if previous is not None:
            os.environ[name] = previous
