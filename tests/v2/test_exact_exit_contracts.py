"""Exact entry-width contracts must not create facts for unobserved variants."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from _helpers import make_lorom_bank0
from v2.cfg_loader import load_bank_cfg
from v2.exit_mx_autoroute import detect_and_route
from v2.program_analysis import NodeDisposition, VariantKey

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools'))
from v2_analyze import _load_cfgs, _declared_exit_modes, build_manifest, build_manifest_native, native_analyzer_path
from v2_regen import _rebuild_callee_exit_mx

CFG = ('bank = 00\nfunc Caller 8000 end:8006 entry_mx:1,0\n'
       'func Callee 8200 end:8201 entry_mx:1,0\nforce_lle 008200\n')
ROM = make_lorom_bank0({0x8000: bytes([0x22, 0x00, 0x82, 0x00, 0xEA, 0x6B]),
                        0x8200: bytes([0x6B])})


def test_exact_contract_parser_rejects_invalid_widths_and_addresses():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw) / 'bank00.cfg'
        for args in ['008200 2 0 1 0', '008200 1 0 -1 0',
                     '1008200 1 0 1 0', '-1 1 0 1 0',
                     '008200 1 0 1', '008200 1 0 1 0 extra',
                     '008200 x 0 1 0']:
            p.write_text(CFG + 'exit_mx_for ' + args + '\n')
            try:
                load_bank_cfg(str(p))
            except ValueError as exc:
                assert 'exit_mx_for' in str(exc)
            else:
                raise AssertionError(args)


def _analyze(directory, suffix, native=False):
    cfg = directory / 'bank00.cfg'
    cfg.write_text(CFG + suffix)
    roots = [VariantKey(pc, m, x) for pc in (0x008000, 0x808000)
             for m in (0, 1) for x in (0, 1)]
    if native:
        rom_path = directory / 'fixture.sfc'
        rom_path.write_bytes(ROM)
        return build_manifest_native(rom_path=rom_path, cfg_dir=directory,
                                     additional_roots=roots)[0]
    return build_manifest(ROM, _load_cfgs(directory), max_insns=128, max_nodes=128,
                          additional_roots=roots)[0]


def _check_exact_scope(manifest):
    for pc in (0x008200, 0x808200):
        for m in (0, 1):
            for x in (0, 1):
                key = VariantKey(pc, m, x)
                assert (key in manifest.exit_modes) == ((m, x) == (1, 0))
                assert key not in manifest.nodes  # force_lle still applies.
    for pc in (0x008000, 0x808000):
        for m in (0, 1):
            for x in (0, 1):
                node = manifest.nodes[VariantKey(pc, m, x)]
                assert (node.disposition == NodeDisposition.AOT_ELIGIBLE) == ((m, x) == (1, 0))


def test_exact_contract_unlocks_only_matching_callers_and_mirrors():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw)
        baseline = _analyze(p, '')
        assert all(n.disposition == NodeDisposition.LLE_ONLY for n in baseline.nodes.values())
        candidate = _analyze(p, 'exit_mx_for 008200 1 0 1 0\n')
        _check_exact_scope(candidate)


def test_native_exact_contract_matches_python():
    if not native_analyzer_path().is_file():
        return
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw)
        exact = 'exit_mx_for 008200 1 0 1 0\n'
        python = _analyze(p, exact)
        native = _analyze(p, exact, native=True)
        _check_exact_scope(native)
        assert native.exit_modes == python.exit_modes
        assert native.roots == python.roots


def test_exact_contract_overrides_broadcast_across_cfg_order():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw)
        (p / 'bank00.cfg').write_text(CFG + 'exit_mx_for 008200 1 0 0 0\n')
        (p / 'bank01.cfg').write_text('bank = 01\nexit_mx_at 008200 1 1\n')
        modes = _declared_exit_modes(_load_cfgs(p))
        for pc in (0x008200, 0x808200):
            assert modes[(pc, 1, 0)] == (0, 0)
            assert modes[(pc, 0, 0)] == (1, 1)
            assert modes[(pc, 1, 1)] == (1, 1)


def test_exact_contract_survives_autoroute_refresh():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw)
        (p / 'bank00.cfg').write_text(CFG + 'exit_mx_for 008200 1 0 0 0\n')
        parsed = _load_cfgs(p)
        cfg = parsed[0][2]
        for _ in range(2):
            cfg.exit_mx_at_per_variant.clear()
            detect_and_route(parsed, ROM)
            modes, *_counts = _rebuild_callee_exit_mx(parsed, {0x008200: {(1, 0), (0, 0)}})
            assert modes[(0x008200, 1, 0)] == (0, 0)
            assert cfg.exit_mx_for == [(0, 0x8200, 1, 0, 0, 0)]
            assert all(route[2:4] != (1, 0) for route in cfg.exit_mx_at_per_variant
                       if route[:2] == (0, 0x8200))


def test_exact_contract_changes_invalidate_published_output_cache():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw); cfg = p / 'cfg'; cfg.mkdir()
        rom = p / 'fixture.sfc'; rom.write_bytes(ROM)
        path = cfg / 'bank00.cfg'; path.write_text(CFG)
        out = p / 'generated'
        cmd = [sys.executable, str(REPO / 'tools/v2_emit.py'), '--rom', str(rom),
               '--cfg-dir', str(cfg), '--out-dir', str(out), '--cfg-roots',
               '--analysis-backend', 'python', '--no-host-root-scan']
        env = {k: v for k, v in os.environ.items() if not k.startswith('SNESRECOMP_')}
        def run():
            r = subprocess.run(cmd, env=env, text=True, capture_output=True)
            assert r.returncode == 0, r.stdout + r.stderr
            return r.stdout, json.loads((out / 'program_manifest.json').read_text())
        _, before = run()
        assert before['nodes']['008000:M1X0']['disposition'] == 'lle_only'
        path.write_text(CFG + 'exit_mx_for 008200 1 0 1 0\n')
        log, after = run()
        assert 'reused verified published output' not in log
        assert after['nodes']['008000:M1X0']['disposition'] == 'aot_eligible'
        assert 'reused verified published output' in run()[0]
        path.write_text(CFG + 'exit_mx_for 008200 0 0 1 0\n')
        log, restored = run()
        assert 'reused verified published output' not in log
        assert restored['nodes']['008000:M1X0']['disposition'] == 'lle_only'
        assert '008200:M1X0' not in restored['exit_modes']
