"""Emission exclusions retain exact exit proofs, never create contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from _helpers import make_lorom_bank0
from v2.program_analysis import VariantKey, NodeDisposition
from v2.program_emit import build_emission_entries
from v2.cfg_loader import load_bank_cfg

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools'))
from v2_analyze import _load_cfgs, build_manifest, build_manifest_native, native_analyzer_path

CFG = ('bank = 00\nfunc Caller 8000 entry_mx:1,0\n'
       'interpret_only 008200\ninterpret_only 008300\n')

def fixture(path, unknown=False):
    (path/'bank00.cfg').write_text(CFG)
    # Two interpreted levels. The leaf changes M, so preservation is wrong.
    rom = make_lorom_bank0({
        0x8000: bytes([0x22, 0, 0x82, 0, 0xEA, 0x6B]),
        0x8200: bytes([0x22, 0, 0x83, 0, 0x6B]),
        0x8300: bytes([0x6C, 0, 0x10]) if unknown else bytes([0xC2, 0x20, 0x6B])})
    roots = [VariantKey(pc, m, x) for pc in (0x008000, 0x808000, 0x808200, 0x808300)
             for m in (0, 1) for x in (0, 1)]
    return rom, roots


def check(manifest, parsed):
    _, emitted, *_ = build_emission_entries(manifest, parsed)
    for bank in (0, 0x80):
        for m in (0, 1):
            for x in (0, 1):
                for pc in (0x8200, 0x8300):
                    key = VariantKey(bank << 16 | pc, m, x)
                    assert manifest.exit_modes[key] == (0, x)
                    assert key.pc24 not in emitted
                assert (m, x) in emitted[bank << 16 | 0x8000]


def test_interpret_only_proves_transitive_exits_for_both_analyzers():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw); rom, roots = fixture(p); parsed = _load_cfgs(p)
        manifest = build_manifest(rom, parsed, max_insns=128, max_nodes=128,
                                  additional_roots=roots)[0]
        check(manifest, parsed)
        if native_analyzer_path().is_file():
            rp = p/'fixture.sfc'; rp.write_bytes(rom)
            native = build_manifest_native(rom_path=rp, cfg_dir=p,
                                           additional_roots=roots)[0]
            check(native, parsed)
            assert manifest.exit_modes == native.exit_modes


def test_interpret_only_does_not_prove_unresolved_exits():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw); rom, roots = fixture(p, unknown=True)
        manifest = build_manifest(rom, _load_cfgs(p), max_insns=128, max_nodes=128,
                                  additional_roots=roots)[0]
        assert not manifest.exit_modes
        assert all(manifest.nodes[key].disposition == NodeDisposition.LLE_ONLY
                   for key in roots if key.pc24 & 0xFFFF == 0x8000)
        (p/'bank00.cfg').write_text(CFG + 'force_lle 008200\n')
        blocked = build_manifest(rom, _load_cfgs(p), max_insns=128, max_nodes=128,
                                 additional_roots=[k for k in roots if k.pc24 & 0xFFFF == 0x8000])[0]
        assert all(k.pc24 & 0xFFFF not in (0x8200, 0x8300) for k in blocked.nodes)


def test_interpret_only_validation():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw)/'bank00.cfg'
        for text in ('', '-1', '1000000', '008200 extra', 'xyz',
                     '008200\ninterpret_only 008200'):
            p.write_text('bank = 00\ninterpret_only ' + text + '\n')
            try:
                load_bank_cfg(str(p))
            except ValueError as exc:
                assert 'interpret_only' in str(exc)
            else:
                raise AssertionError(text)


def test_interpret_only_cache_and_null_dispatch():
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw); cfg = p/'cfg';cfg.mkdir();rom,_ = fixture(cfg)
        rp = p/'fixture.sfc';rp.write_bytes(rom);out=p/'generated'
        cmd=[sys.executable,str(REPO/'tools/v2_emit.py'),'--rom',str(rp),
             '--cfg-dir',str(cfg),'--out-dir',str(out),'--cfg-roots',
             '--analysis-backend','python','--no-host-root-scan']
        env={k:v for k,v in os.environ.items() if not k.startswith('SNESRECOMP_')}
        legacy = [sys.executable, str(REPO/'tools/v2_regen.py'), '--rom', str(rp),
                  '--cfg-dir', str(cfg), '--out-dir', str(p/'legacy')]
        rejected = subprocess.run(legacy, env=env, text=True, capture_output=True)
        assert rejected.returncode != 0
        assert 'interpret_only requires manifest-driven' in rejected.stderr
        assert not (p/'legacy/dispatch_v2.c').exists()
        def run():
            r=subprocess.run(cmd,env=env,text=True,capture_output=True)
            assert r.returncode==0,r.stdout+r.stderr
            return r.stdout,(out/'dispatch_v2.c').read_text()
        _,a=run()
        assert 'bank_00_8200_M1X0' not in a and 'bank_00_8300_M1X0' not in a
        assert 'reused verified published output' in run()[0]
        (cfg/'bank00.cfg').write_text(CFG.replace('interpret_only 008200\n',''))
        log,b=run()
        assert 'reused verified published output' not in log
        assert 'bank_00_8200_M1X0' in b and 'bank_00_8300_M1X0' not in b
