import json
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "tier2_ingest.py"


def run_ingest(tmp_path, discoveries, cfg_text):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "bank00.cfg").write_text(cfg_text, encoding="utf-8")
    manifest = tmp_path / "tier2.json"
    manifest.write_text(json.dumps({
        "schema": "snesrecomp tier2 coverage v1",
        "rom_title": "synthetic",
        "discoveries": discoveries,
    }), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOL), str(manifest),
         "--cfg-dir", str(cfg_dir)],
        text=True, capture_output=True, check=True).stdout


def discovery(site, target, mx, kind, *, clean=1, bail=0):
    return {
        "site_pc24": site,
        "target_pc24": target,
        "entry_mx": mx,
        "site_kind": kind,
        "clean_hits": clean,
        "bail_hits": bail,
    }


def test_report_distinguishes_variants_calls_indirects_and_landings(tmp_path):
    output = run_ingest(tmp_path, [
        discovery("0x008029", "0x00F806", "M1X1", "call_gap"),
        discovery("0x008040", "0x00A000", "M1X1", "call_gap"),
        discovery("0x008050", "0x00A100", "M1X1", "call_gap"),
        discovery("0x00C689", "0x00C6E7", "M1X1", "indirect_goto"),
        discovery("0x0083B4", "0x0086FC", "M1X0",
                  "indirect_dispatch"),
        discovery("0x0088DC", "0x008D4B", "M1X1", "goto_gap"),
        discovery("0x008060", "0x00A200", "M1X1", "call_gap",
                  clean=0, bail=1),
    ], """\
bank = 00
func KnownFunc f806 entry_mx:0,0
func Existing a000 entry_mx:1,1
""")

    assert "-- MISSING EXACT CALL VARIANTS: 1 site(s) --" in output
    assert "target $00F806 M1X1; cfg has M0X0" in output
    assert "-- DECLARED CALL GAPS: 1 site(s) --" in output
    assert "target $00A000 M1X1" in output
    assert "-- OPTIONAL CALL BOUNDARIES: 1 unnamed clean target(s) --" in output
    assert "func bank_00_A100 a100" in output
    assert "-- INDIRECT GOTO SITES TO REVIEW: 1 site(s) --" in output
    assert "site $00C689 -> target $00C6E7" in output
    assert "-- JUMP/RETURN LANDINGS TO REVIEW: 2 site(s) --" in output
    assert "site $0083B4 -> target $0086FC" in output
    assert "site $0088DC -> target $008D4B" in output
    assert "-- INVESTIGATE: 1 bailed site(s)" in output
    assert "SITE NEEDS DISPATCH AUTHORIZATION" not in output


def test_entry_mx_at_is_applied_as_final_exact_variant_override(tmp_path):
    output = run_ingest(tmp_path, [
        discovery("0x008029", "0x00F806", "M1X1", "call_gap"),
    ], """\
bank = 00
entry_mx_at f806 1 1
func KnownFunc f806 entry_mx:0,0
""")

    assert "-- MISSING EXACT CALL VARIANTS: 0 site(s) --" in output
    assert "-- DECLARED CALL GAPS: 1 site(s) --" in output
    assert "target $00F806 M1X1" in output
