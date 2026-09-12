"""Experimental generated bus-clock accounting, enabled at generation time.

Every CPU cycle starts at six master clocks. Add only the wait-state excess
for instruction fetches and emitted architectural data/stack accesses. Keep
host prologue inspection reads outside this transformation.
"""
import os
import re


def enabled():
    return bool(os.environ.get("SNESRECOMP_EMIT_BUS_TIMING"))


INSTRUCTION_ENV = "SNESRECOMP_EMIT_INSTRUCTION_TIMING"


def instruction_targets():
    """Explicit native leaf entry keys, written as HEXPC:M:X, comma separated."""
    value = os.environ.get(INSTRUCTION_ENV, "")
    result = set()
    for item in value.split(",") if value else ():
        if not re.fullmatch(r"[0-9A-Fa-f]{6}:[01]:[01]", item):
            raise ValueError(f"{INSTRUCTION_ENV}: invalid exact entry key {item!r}")
        pc, m, x = item.split(":")
        result.add((int(pc, 16), int(m), int(x)))
    return frozenset(result)


def validate_instruction_leaf(block_pairs):
    """Fail closed until calls, branches and stack transforms are supported."""
    from snes65816 import IMP, IMM, ABS, ABS_X, ABS_Y, LONG, LONG_X, DP, DP_X, DP_Y
    if len(block_pairs) != 1:
        raise ValueError("instruction timing requires a single straight-line leaf block")
    pairs = next(iter(block_pairs.values()))
    if not pairs or pairs[-1][0].mnem not in ("RTS", "RTL"):
        raise ValueError("instruction timing requires a final RTS or RTL")
    for insn, _ in pairs[:-1]:
        if (insn.mnem not in ("NOP", "LDA", "LDX", "LDY", "STA", "STX", "STY", "STZ")
                or insn.mode not in (IMP, IMM, ABS, ABS_X, ABS_Y, LONG, LONG_X,
                                     DP, DP_X, DP_Y)):
            raise ValueError(f"instruction timing does not yet support {insn.mnem} at {insn.addr:06X}")
    return pairs


class BusTimingLines(list):
    def __init__(self):
        super().__init__()
        self.pc = 0
        self.audit = int(bool(os.environ.get("SNESRECOMP_EMIT_EVENT_CROSSING_AUDIT")))

    def append(self, line):
        line = re.sub(r"\bcpu_(read|write)(8|16)\(cpu,\s*",
                      lambda m: f"cpu_aot_{m[1]}{m[2]}(cpu, 0x{self.pc:06X}u, {self.audit}, ",
                      line)
        super().append(line)

    def extend(self, lines):
        for line in lines:
            self.append(line)

    def instruction(self, insn):
        self.pc = insn.addr & 0xFFFFFF
        # Fetches are modelled, not executed: generated instructions contain
        # their operands as literals. Sample MEMSEL here, not once per block.
        self.append(f"cpu_aot_fetch_extra(cpu, 0x{self.pc:06X}u, {insn.length}, {self.audit});")


class InstructionTimingLines(BusTimingLines):
    """Accumulate one opcode's costs while callbacks see its start clock."""
    def append(self, line):
        line = re.sub(r"\bcpu_(read|write)(8|16)\(cpu,\s*",
                      lambda m: f"cpu_aot_insn_{m[1]}{m[2]}(cpu, &_aot_timing, ", line)
        line = line.replace("cpu->cycles +=", "_aot_timing.cycles +=")
        line = line.replace("cpu->master_cycles +=", "_aot_timing.master +=")
        list.append(self, line)

    def instruction(self, insn, cycles):
        self.pc = insn.addr & 0xFFFFFF
        self.extend([
            "if (interp_bridge_lle_master_deadline_reached(cpu)) {",
            f"  return interp_bridge_lle_yield_unwind(cpu, 0x{self.pc:06X}u);",
            "}",
            "cpu->coprocessor_master_cycles = cpu->master_cycles;",
            f"_aot_timing = (CpuAotInstructionTiming){{{cycles}, {cycles * 6}}};",
            f"cpu_aot_insn_bus_extra(&_aot_timing, {self.pc >> 16}, "
            f"0x{self.pc & 0xFFFF:04X}, {insn.length});",
        ])

    def commit(self):
        return f"cpu_aot_insn_commit(cpu, &_aot_timing, 0x{self.pc:06X}u, {self.audit});"
