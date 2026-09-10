"""Dispatch-helper probes must decode at the caller's exact M/X state."""

from v2.decoder import classify_dispatch_helper  # noqa: E402


def test_x16_call_does_not_accept_x8_misalignment_as_dispatch_helper():
    rom = bytearray(0x8000)
    # Invented helper-shaped routine. With X=0, LDX consumes its full 16-bit
    # immediate and the body has no stack pull, so this is not an ExecutePtr
    # dispatcher. Misdecoding it at X=1 leaves the immediate's high byte in
    # the opcode stream and turns $68 in the following STX address into PLA,
    # producing a false structural match.
    body = bytes([
        0x22, 0x00, 0x81, 0x00,       # JSL $00:8100
        0xA2, 0x00, 0x00,             # LDX #$0000 (X=0)
        0x8E, 0x68, 0x12,             # STX $1268
        0x8E, 0x6A, 0x12,             # STX $126A
        0xA9, 0x00,                   # LDA #$00
        0xEB,                         # XBA
        0xB9, 0x01, 0x12,             # LDA $1201,Y
        0x0A, 0xAA,                   # ASL A / TAX
        0xBF, 0x00, 0x82, 0x00,       # LDA $00:8200,X
        0x85, 0x20,                   # STA $20
        0xBF, 0x01, 0x82, 0x00,       # LDA $00:8201,X
        0x85, 0x21,                   # STA $21
        0x6C, 0x20, 0x00,             # JMP ($0020)
    ])
    rom[:len(body)] = body

    assert classify_dispatch_helper(bytes(rom), 0, 0x8000, 1, 0) is None
    # This assertion pins the motivating ambiguity: the same bytes are a
    # false positive if a caller ignores its known X=0 state.
    assert classify_dispatch_helper(bytes(rom), 0, 0x8000, 1, 1) == "short"
