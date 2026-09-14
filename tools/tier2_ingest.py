#!/usr/bin/env python3
"""
tier2_ingest.py -- audit an interpreter-tier coverage manifest.

Phase 3 of the interpreter-fallback tier (see docs/MULTI_TIER.md). Reads a
Tier-2 coverage manifest (default: build/tier2_coverage.json, schema
"snesrecomp tier2 coverage v1") that the runner writes on exit. The
manifest-driven emitter consumes clean targets directly with
`v2_emit.py --profile-manifest`; profile roots select optional AOT work while
LLE remains authoritative. This audit also proposes optional function
boundaries for unnamed targets and flags sites that need human inspection.

Human-in-the-loop BY DESIGN -- like the existing cfg_override_* proposers, it
PRINTS paste-ready directives; it does not edit cfgs. A human stays between
"observed at runtime" and "trusted as code," which is the project discipline
(no laundering a runtime mis-execution into a static translation).

Discoveries split into evidence-specific buckets:

  CALL GAP     A direct call reached a missing exact (PC,M,X) variant, an
               existing LLE-only variant, or an unnamed callable boundary.
               These are profile candidates, not evidence of an indirect
               dispatch site.

  INDIRECT     An indirect JMP selected a runtime target. The site may need a
               reviewed finite-target cfg contract. Runtime observation alone
               does not prove that the observed set is complete.

  LANDING      A goto or computed return landed at a missing exact entry.
               This can be an internal label or return continuation, so it is
               reported for review and never proposed as a function
               automatically.

  INVESTIGATE  The interpreter BAILED (bail_hits>0): it could not run the
               target -- e.g. a garbage indirect target from upstream recomp-
               state corruption (SM's JMP ($0012)=$FFFF is the canonical case).
               These are BUG LEADS, never promotion candidates. Ranked by bail
               count, then earliest frame.

Site kinds (2026-07-02 additions): besides the tier-down kinds
(indirect_dispatch / indirect_goto / bank_miss), the bridge now records
in-bridge sightings -- `call_gap` (an interpreted JSR/JSL whose target has no
compiled variant) and `goto_gap` (an indirect JMP/JML landing with none).
Both are always clean observations. `call_gap` targets can become optional
profile roots; a `goto_gap` target is promoted only when cfg independently
declares it as a function boundary. A goto landing can be a mid-function label
or return continuation, so runtime observation alone must not manufacture a
call ABI. Addresses are LoROM-canonicalized (exec mirrors $80-$BF recorded as
$00-$3F).

Usage:
  python tools/tier2_ingest.py [manifest.json] [--cfg-dir recomp]
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

FUNC_RE = re.compile(r'^\s*func\s+(\S+)\s+([0-9A-Fa-f]+)')
ENTRY_MX_RE = re.compile(r'\bentry_mx:([01]),([01])\b')
ENTRY_MX_AT_RE = re.compile(
    r'^\s*entry_mx_at\s+([0-9A-Fa-f]+)\s+([01])\s+([01])(?:\s|$)')
BANK_FILE_RE = re.compile(r'bank([0-9A-Fa-f]{2})\.cfg$')


def load_manifest(path):
    with open(path, 'r', encoding='utf-8') as f:
        m = json.load(f)
    schema = m.get('schema', '')
    if not schema.startswith('snesrecomp tier2 coverage'):
        sys.stderr.write(f"warning: unexpected schema {schema!r}\n")
    return m


def parse_pc24(s):
    """'0x0FE8B7' / '0FE8B7' / 1042103 -> int."""
    if isinstance(s, int):
        return s & 0xFFFFFF
    return int(str(s), 16) & 0xFFFFFF


def scan_cfg_funcs(cfg_dir):
    """Return (func_variants, bank_files):
       func_variants[bank][pc16] = set of declared exact (M, X) variants;
       bank_files[bank] = path to that bank's cfg (for the paste hint)."""
    func_variants = defaultdict(lambda: defaultdict(set))
    bank_files = {}
    if not os.path.isdir(cfg_dir):
        sys.stderr.write(f"warning: cfg dir {cfg_dir!r} not found -- "
                         f"can't dedup against existing funcs\n")
        return func_variants, bank_files
    for name in sorted(os.listdir(cfg_dir)):
        mb = BANK_FILE_RE.search(name)
        if not mb:
            continue
        bank = int(mb.group(1), 16)
        path = os.path.join(cfg_dir, name)
        bank_files[bank] = path
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = list(f)

        # Match cfg_loader: entry_mx_at is a final per-address override, even
        # when the func line itself contains entry_mx.
        overrides = {}
        for line in lines:
            match = ENTRY_MX_AT_RE.match(line)
            if match:
                overrides[int(match.group(1), 16) & 0xFFFF] = (
                    int(match.group(2)), int(match.group(3)))

        for line in lines:
            match = FUNC_RE.match(line)
            if not match:
                continue
            pc16 = int(match.group(2), 16) & 0xFFFF
            mx_match = ENTRY_MX_RE.search(line)
            mx = ((int(mx_match.group(1)), int(mx_match.group(2)))
                  if mx_match else (1, 1))
            func_variants[bank][pc16].add(overrides.get(pc16, mx))
    return func_variants, bank_files


def parse_mx(value):
    """Return (M, X) for a manifest value such as M1X0, else None."""
    match = re.fullmatch(r'M([01])X([01])', str(value))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('manifest', nargs='?', default='build/tier2_coverage.json',
                    help='Tier-2 coverage manifest (default: %(default)s)')
    ap.add_argument('--cfg-dir', default='recomp',
                    help='cfg directory to dedup against (default: %(default)s)')
    ap.add_argument('--min-hits', type=int, default=1,
                    help='ignore discoveries below this total hit count')
    args = ap.parse_args()

    if not os.path.isfile(args.manifest):
        sys.stderr.write(f"error: manifest {args.manifest!r} not found. Run the "
                         f"game once (it writes the manifest on exit), then "
                         f"re-run this tool.\n")
        return 2

    m = load_manifest(args.manifest)
    func_variants, bank_files = scan_cfg_funcs(args.cfg_dir)
    discoveries = m.get('discoveries', [])

    promote_func = defaultdict(list)  # unnamed clean direct-call targets
    missing_variants = []            # known func, absent exact call variant
    declared_call_gaps = []          # exact func exists but remains LLE-only
    indirect_gotos = []              # actual pointer-selected JMP/JML target
    landing_reviews = []             # goto/return continuation observations
    unclassified_clean = []
    investigate = []                 # bailed observations
    seen_promote = set()              # (bank, addr16) dedup

    for d in discoveries:
        clean = int(d.get('clean_hits', 0))
        bail = int(d.get('bail_hits', 0))
        if clean + bail < args.min_hits:
            continue
        target = parse_pc24(d['target_pc24'])
        bank, addr16 = target >> 16, target & 0xFFFF
        if bail > 0:
            investigate.append(d)
            continue
        kind = str(d.get('site_kind', ''))
        variants = func_variants.get(bank, {}).get(addr16, set())
        mx = parse_mx(d.get('entry_mx'))

        if kind == 'call_gap' and not variants:
            key = (bank, addr16)
            if key not in seen_promote:
                seen_promote.add(key)
                promote_func[bank].append((addr16, d))
        elif kind == 'call_gap' and mx is not None and mx not in variants:
            missing_variants.append((d, variants))
        elif kind == 'call_gap':
            declared_call_gaps.append((d, variants))
        elif kind == 'indirect_goto':
            indirect_gotos.append(d)
        elif kind in ('goto_gap', 'indirect_dispatch'):
            landing_reviews.append(d)
        else:
            unclassified_clean.append(d)

    # -- report ------------------------------------------------------------
    out = sys.stdout.write
    out("=" * 72 + "\n")
    out(f"Tier-2 gap manifest ingest -- {m.get('rom_title', '?')}\n")
    out(f"  manifest: {args.manifest}\n")
    out(f"  total tier hits: {m.get('total_tier_hits', '?')}   "
        f"distinct (site,target,mx): {m.get('distinct_sites', len(discoveries))}"
        f"   overflowed tuples: {m.get('overflowed_tuples', 0)}\n")
    out("=" * 72 + "\n\n")

    if not discoveries:
        out("No discoveries -- the interpreter tier never fired this run.\n"
            "(For a fully-covered game that's the expected dormant state.)\n")
        return 0

    # A direct call establishes a callable boundary, but naming/slicing it in
    # cfg remains optional because the profile can materialize it directly.
    n_promote = sum(len(v) for v in promote_func.values())
    out("AOT optimization: pass this file to v2_emit.py with "
        "`--profile-manifest`.\n"
        "Clean target/MX observations become optional AOT roots; bails are "
        "excluded.\n\n")
    out(f"-- OPTIONAL CALL BOUNDARIES: {n_promote} unnamed clean target(s) --\n")
    if not n_promote:
        out("  (none)\n")
    for bank in sorted(promote_func):
        cfg = bank_files.get(bank)
        hint = cfg if cfg else f"bank{bank:02x}.cfg  (NOT FOUND -- create it)"
        out(f"\n  # -> {hint}\n")
        for addr16, d in sorted(promote_func[bank]):
            kind = d.get('site_kind', '?')
            site = parse_pc24(d['site_pc24'])
            # entry_mx is REQUIRED on the emitted line. The `func` directive
            # defaults to entry_mx:1,1 (cfg_loader.py), and these observations
            # are overwhelmingly M0X0, so a line pasted without it silently
            # seeds the WRONG variant: the declared boundary is analyzed at a
            # width the guest never enters it at, and the variant that actually
            # runs stays interpreted. Emit the observed mode explicitly.
            mx = str(d.get('entry_mx', ''))
            m_ = re.fullmatch(r"M([01])X([01])", mx)
            if m_:
                mx_tok = f" entry_mx:{m_.group(1)},{m_.group(2)}"
                mx_note = ""
            else:
                mx_tok = ""
                mx_note = "  << NO entry_mx OBSERVED: defaults to 1,1 -- CHECK"
            out(f"  func bank_{bank:02X}_{addr16:04X} {addr16:04x}{mx_tok}"
                f"    # {mx or '?'} {kind}, "
                f"{int(d.get('clean_hits',0))} clean hit(s), "
                f"from site $%06X, first frame {d.get('first_frame','?')}"
                f"{mx_note}\n"
                % site)


    out(f"\n-- MISSING EXACT CALL VARIANTS: {len(missing_variants)} site(s) --\n")
    out("  (the function address is declared, but not at the observed M/X.\n"
        "   --profile-manifest can materialize the exact observed variant.)\n")
    if not missing_variants:
        out("  (none)\n")
    for d, variants in sorted(
            missing_variants,
            key=lambda item: -int(item[0].get('clean_hits', 0))):
        site = parse_pc24(d['site_pc24'])
        target = parse_pc24(d['target_pc24'])
        declared = ','.join(f"M{m}X{x}" for m, x in sorted(variants))
        out("  call $%06X -> target $%06X %s; cfg has %s "
            "(%d clean hit(s))\n" % (
                site, target, d.get('entry_mx', '?'), declared or 'no variant',
                int(d.get('clean_hits', 0))))

    out(f"\n-- DECLARED CALL GAPS: {len(declared_call_gaps)} site(s) --\n")
    out("  (the exact cfg variant already exists; inspect its disposition and\n"
        "   reasons in program_manifest.json. This is not dispatch evidence.)\n")
    if not declared_call_gaps:
        out("  (none)\n")
    for d, _variants in sorted(
            declared_call_gaps,
            key=lambda item: -int(item[0].get('clean_hits', 0))):
        site = parse_pc24(d['site_pc24'])
        target = parse_pc24(d['target_pc24'])
        out("  call $%06X -> target $%06X %s (%d clean hit(s))\n" % (
            site, target, d.get('entry_mx', '?'),
            int(d.get('clean_hits', 0))))

    out(f"\n-- INDIRECT GOTO SITES TO REVIEW: {len(indirect_gotos)} site(s) --\n")
    out("  (verify the pointer source and complete finite target set before\n"
        "   adding an indirect_dispatch contract; never infer completeness\n"
        "   from observed targets alone.)\n")
    if not indirect_gotos:
        out("  (none)\n")
    for d in sorted(indirect_gotos,
                    key=lambda item: -int(item.get('clean_hits', 0))):
        site = parse_pc24(d['site_pc24'])
        target = parse_pc24(d['target_pc24'])
        out("  site $%06X -> target $%06X (%s, %d clean hit(s))\n" % (
            site, target, d.get('entry_mx', '?'),
            int(d.get('clean_hits', 0))))

    out(f"\n-- JUMP/RETURN LANDINGS TO REVIEW: {len(landing_reviews)} site(s) --\n")
    out("  (may be an internal label or computed return continuation; no\n"
        "   function-boundary or dispatch contract is proposed automatically.)\n")
    if not landing_reviews:
        out("  (none)\n")
    for d in sorted(landing_reviews,
                    key=lambda item: -int(item.get('clean_hits', 0))):
        site = parse_pc24(d['site_pc24'])
        target = parse_pc24(d['target_pc24'])
        out("  site $%06X -> target $%06X (%s %s, %d clean hit(s))\n" % (
            site, target, d.get('entry_mx', '?'), d.get('site_kind', '?'),
            int(d.get('clean_hits', 0))))

    if unclassified_clean:
        out(f"\n-- UNCLASSIFIED CLEAN OBSERVATIONS: "
            f"{len(unclassified_clean)} site(s) --\n")
        for d in unclassified_clean:
            site = parse_pc24(d['site_pc24'])
            target = parse_pc24(d['target_pc24'])
            out("  site $%06X -> target $%06X (%s %s)\n" % (
                site, target, d.get('entry_mx', '?'),
                d.get('site_kind', '?')))

    # INVESTIGATE: bailed sites are bug leads, not promotion candidates.
    out(f"\n-- INVESTIGATE: {len(investigate)} bailed site(s) "
        f"(bug leads, NOT promoted) --\n")
    out("  (the interpreter could not run the target -- likely an upstream\n"
        "   recomp-state bug, e.g. a garbage indirect target. Do NOT promote;\n"
        "   chase who corrupts the state that feeds this site.)\n")
    if not investigate:
        out("  (none)\n")
    for d in sorted(investigate,
                    key=lambda d: (-int(d.get('bail_hits', 0)),
                                   int(d.get('first_frame', 1 << 30)))):
        site = parse_pc24(d['site_pc24'])
        target = parse_pc24(d['target_pc24'])
        out(f"  site $%06X -> target $%06X  (%s %s, %d bail(s)/%d clean, "
            "first frame %s)\n"
            % (site, target, d.get('entry_mx', '?'), d.get('site_kind', '?'),
               int(d.get('bail_hits', 0)), int(d.get('clean_hits', 0)),
               d.get('first_frame', '?')))

    out("\n" + "=" * 72 + "\n")
    out("Regenerate with `--profile-manifest` and rebuild. Add a printed func\n"
        "only when its boundary improves naming/slicing; `--cfg-roots` decides\n"
        "whether declared funcs are also static roots. LLE remains the fallback\n"
        "for every absent or rejected exact variant (see MULTI_TIER.md sec 3a).\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
