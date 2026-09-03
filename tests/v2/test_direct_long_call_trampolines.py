"""Synthetic coverage for source-assembled long-call trampoline idioms."""

from _helpers import make_lorom_bank0 as _unused_path_setup  # noqa: F401,E402

from v2.decoder import decode_function, analyze_function_exit_mx  # noqa: E402
from v2.emit_function import emit_function  # noqa: E402
from v2.program_analysis import EdgeKind, summarize_decode_graph  # noqa: E402


def _rom(blobs):
    image = bytearray([0xEA] * 0x10000)
    for pc24, data in blobs.items():
        bank = (pc24 >> 16) & 0x7F
        pc = pc24 & 0xFFFF
        off = bank * 0x8000 + pc - 0x8000
        image[off:off + len(data)] = data
    return bytes(image)


def test_phk_per_jml_is_a_returning_long_call():
    rom = _rom({
        0x008000: bytes([
            0x08,                    # PHP
            0xE2, 0x30,              # SEP #$30
            0x4B,                    # PHK
            0x62, 0x03, 0x00,        # PER return-1 ($800A)
            0x5C, 0x00, 0x90, 0x01,  # JML $01:9000
            0x28,                    # PLP (resume at $800B)
            0x60,                    # RTS
        ]),
        0x019000: bytes([0x6B]),      # RTL
    })
    exits = {(0x019000, 1, 1): (1, 1)}
    graph = decode_function(
        rom, 0, 0x8000, 0, 1, end=0x800D,
        callee_exit_mx=exits, stop_on_unknown_callee_exit=True)

    jml = next(di.insn for di in graph.insns.values()
               if di.insn.addr == 0x008007)
    assert jml.return_trampoline
    assert jml.return_trampoline_pc == 0x800B
    assert analyze_function_exit_mx(graph) == (0, 1)

    summary = summarize_decode_graph(graph)
    target_edges = [edge for edge in summary.demands
                    if edge.target and edge.target.pc24 == 0x019000]
    assert [edge.kind for edge in target_edges] == [EdgeKind.DIRECT_CALL]

    src = emit_function(
        rom, 0, 0x8000, 0, 1, end=0x800D,
        func_name="return_trampoline", callee_exit_mx=exits)
    assert "PHK;PER;JML return trampoline -> long call" in src
    assert "trampoline setup PHK skipped" in src
    assert "trampoline setup PER skipped" in src
    assert "cpu->host_return_valid = 3" in src
    assert "cpu->PB = 0x01" in src


def test_phk_jsr_jml_veneer_is_folded_to_direct_long_call():
    rom = _rom({
        0x008000: bytes([
            0x4B,              # PHK
            0x20, 0x00, 0x82,  # JSR $8200
            0x60,              # RTS
        ]),
        0x008200: bytes([
            0x5C, 0x00, 0x90, 0x01,  # JML $01:9000
        ]),
        0x019000: bytes([0x6B]),      # RTL
    })
    exits = {(0x019000, 1, 1): (1, 1)}
    graph = decode_function(
        rom, 0, 0x8000, 1, 1, end=0x8005,
        callee_exit_mx=exits, stop_on_unknown_callee_exit=True)

    jsr = next(di.insn for di in graph.insns.values()
               if di.insn.addr == 0x008001)
    assert jsr.long_call_trampoline_target == 0x019000
    assert analyze_function_exit_mx(graph) == (1, 1)

    summary = summarize_decode_graph(graph)
    targets = {(edge.kind, edge.target.pc24) for edge in summary.demands
               if edge.target is not None}
    assert targets == {(EdgeKind.DIRECT_CALL, 0x019000)}

    src = emit_function(
        rom, 0, 0x8000, 1, 1, end=0x8005,
        func_name="long_call_veneer", callee_exit_mx=exits)
    assert "PHK;JSR long-call veneer -> long call" in src
    assert "trampoline setup PHK skipped" in src
    assert "cpu->host_return_valid = 3" in src
    assert "bank_01_9000" in src
    assert "bank_00_8200" not in src


def test_phk_jsr_pea_jml_veneer_gets_a_long_outer_frame():
    rom = _rom({
        0x008000: bytes([
            0x4B,              # PHK
            0x20, 0x00, 0x82,  # JSR $8200
            0x60,              # RTS
        ]),
        0x008200: bytes([
            0xF4, 0xFB, 0x90,        # PEA $90FB (shared RTL - 1)
            0x5C, 0x00, 0x91, 0x01,  # JML $01:9100 (RTS body)
        ]),
        0x0190FC: bytes([0x6B]),      # shared Return_long: RTL
        0x019100: bytes([0x60]),      # short-return body: RTS
    })
    exits = {(0x008200, 1, 1): (1, 1)}
    graph = decode_function(
        rom, 0, 0x8000, 1, 1, end=0x8005,
        callee_exit_mx=exits, stop_on_unknown_callee_exit=True)

    jsr = next(di.insn for di in graph.insns.values()
               if di.insn.addr == 0x008001)
    assert jsr.long_call_trampoline_target == 0x008200
    assert analyze_function_exit_mx(graph) == (1, 1)

    summary = summarize_decode_graph(graph)
    targets = {(edge.kind, edge.target.pc24) for edge in summary.demands
               if edge.target is not None}
    assert targets == {(EdgeKind.DIRECT_CALL, 0x008200)}

    src = emit_function(
        rom, 0, 0x8000, 1, 1, end=0x8005,
        func_name="pea_long_call_veneer", callee_exit_mx=exits)
    assert "PHK;JSR long-call veneer -> long call" in src
    assert "trampoline setup PHK skipped" in src
    assert "cpu->host_return_valid = 3" in src
    assert "bank_00_8200" in src
