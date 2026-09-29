"""Experimental generated bus-clock accounting, enabled at generation time.

Every CPU cycle starts at six master clocks. Add only the wait-state excess
for instruction fetches and emitted architectural data/stack accesses. Keep
host prologue inspection reads outside this transformation.
"""
import os
import re
from contextvars import ContextVar
from functools import wraps
from v2 import continuations


BUS_TARGETS_ENV = "SNESRECOMP_EMIT_BUS_TIMING_TARGETS"
_selected_bus_timing = ContextVar("selected_bus_timing", default=False)
_selected_continuation = ContextVar("selected_continuation", default=False)
_selected_instruction_timing = ContextVar("selected_instruction_timing", default=False)


def instruction_enabled():
    return _selected_instruction_timing.get()


def linear_word_read(insn):
    """The newly supported word ORA abs,Y uses a 24-bit data address."""
    return instruction_enabled() and insn.opcode == 0x19 and insn.m_flag == 0


def continuation_enabled():
    return _selected_continuation.get()


def enabled():
    return bool(os.environ.get("SNESRECOMP_EMIT_BUS_TIMING")) or _selected_bus_timing.get()


INSTRUCTION_ENV = "SNESRECOMP_EMIT_INSTRUCTION_TIMING"


def instruction_targets():
    """Explicit native entry keys, written as HEXPC:M:X, comma separated."""
    return _targets(INSTRUCTION_ENV)


def bus_targets():
    """Exact entries using bus costs with the existing block timing model."""
    return _targets(BUS_TARGETS_ENV)


def _targets(env_name):
    value = os.environ.get(env_name, "")
    result = set()
    for item in value.split(",") if value else ():
        if not re.fullmatch(r"[0-9A-Fa-f]{6}:[01]:[01]", item):
            raise ValueError(f"{env_name}: invalid exact entry key {item!r}")
        pc, m, x = item.split(":")
        result.add((int(pc, 16), int(m), int(x)))
    return frozenset(result)


def scope_function(emitter):
    """Keep the exact entry selection active through nested codegen helpers."""
    @wraps(emitter)
    def scoped(rom, bank, start, entry_m, entry_x, **kwargs):
        key = (((bank & 0xFF) << 16) | (start & 0xFFFF), entry_m, entry_x)
        token = _selected_bus_timing.set(key in bus_targets())
        resume_token = _selected_continuation.set(key in continuations.selections())
        instruction_token = _selected_instruction_timing.set(key in instruction_targets())
        try:
            return emitter(rom, bank, start, entry_m, entry_x, **kwargs)
        finally:
            _selected_instruction_timing.reset(instruction_token)
            _selected_continuation.reset(resume_token)
            _selected_bus_timing.reset(token)
    return scoped


def validate_instruction_leaf(block_pairs, cfg):
    """Accept native bodies with tested control flow, arithmetic and status."""
    from snes65816 import (IMP, ACC, IMM, ABS, ABS_X, ABS_Y, LONG, LONG_X,
                          DP, DP_X, DP_Y, REL, REL16, INDIR_LY)
    from v2.ir import Call, Goto
    def direct_long_tail(insn, ops):
        return (insn.mnem == 'JMP' and insn.mode == LONG
                and not getattr(insn, 'dispatch_entries', None)
                and not getattr(insn, 'dispatch_runtime', False)
                and not getattr(insn, 'return_trampoline', False)
                and any(isinstance(op, Goto) for op in ops))
    data_modes = (IMM, ABS, ABS_X, ABS_Y, LONG, LONG_X, DP, DP_X, DP_Y)
    branches = ("BEQ", "BNE", "BCC", "BCS", "BMI", "BPL", "BVC", "BVS", "BRA", "BRL")
    returns = 0
    for key, pairs in block_pairs.items():
        if not pairs:
            raise ValueError("instruction timing requires nonempty blocks")
        successors = cfg.blocks[key].successors
        if any(s not in block_pairs for s in successors):
            raise ValueError("instruction timing requires local branch targets")
        if (not successors and pairs[-1][0].mnem not in ("RTS", "RTL")
                and not direct_long_tail(*pairs[-1])):
            raise ValueError("instruction timing requires a final RTS, RTL or direct JML")
        for insn, ops in pairs:
            direct_call = (insn.mnem in ("JSR", "JSL") and insn.mode in (ABS, LONG)
                           and not getattr(insn, 'dispatch_entries', None)
                           and not getattr(insn, 'dispatch_runtime', False)
                           and not getattr(insn, 'long_call_trampoline_target', None)
                           and any(isinstance(op, Call) and not op.indirect
                                   and not op.terminal and not op.noreturn
                                   and op.target is not None for op in ops))
            supported = (
                (insn.mnem in ("RTS", "RTL", "NOP", "CLC", "SEC", "DEY", "INY", "INX", "DEX", "TYA", "XBA")
                 and insn.mode == IMP)
                or (insn.mnem in ("PHA", "PLA") and insn.mode == IMP)
                or (insn.mnem in ("PHX", "PLX", "PHY", "PLY") and insn.mode == IMP
                    and insn.x_flag == 0)
                or (insn.mnem in ("TXA", "TAX", "TAY") and insn.mode == IMP
                    and insn.m_flag == 0 and insn.x_flag == 0)
                # Index-width changes need separate narrowing/resume support.
                or (insn.mnem in ("REP", "SEP") and insn.mode == IMM
                    and not (insn.operand & 0x10))
                or (insn.mnem in ("LDA", "LDX", "LDY", "STA", "STX", "STY", "STZ",
                                  "CMP", "ADC", "AND", "EOR", "BIT")
                    and insn.mode in data_modes)
                or (insn.mnem == "ORA" and insn.mode in (IMM, DP))
                or (insn.mnem == "ORA" and insn.mode == ABS_Y and insn.m_flag == 0)
                or (insn.mnem in ("LDA", "SBC") and insn.mode == INDIR_LY
                    and insn.m_flag == 1 and insn.x_flag == 0)
                or (insn.mnem == "CPX" and insn.mode in (IMM, DP, ABS))
                or (insn.mnem == "SBC" and insn.mode in (IMM, ABS))
                or (insn.mnem == "SBC" and insn.mode == DP)
                # Only these tested word RMW forms use reverse byte writes.
                or (insn.mnem in ("ROL", "LSR", "DEC") and insn.mode == DP
                    and insn.m_flag == 0)
                or (insn.mnem in ("INC", "DEC", "ASL", "ROL") and insn.mode == DP
                    and insn.m_flag == 1)
                or (insn.mnem in ("ASL", "INC") and insn.mode == ACC)
                or (insn.mnem == "ROR" and insn.mode == ACC and insn.m_flag == 0)
                or (insn.mnem == "LSR" and insn.mode == ACC and insn.m_flag == 0)
                or (insn.mnem in branches and insn.mode in (REL, REL16))
                or (insn.mnem == "JMP" and insn.mode == ABS
                    and len(successors) == 1 and successors[0] in block_pairs
                    and any(isinstance(op, Goto) for op in ops)
                    and not getattr(insn, 'dispatch_entries', None)
                    and not getattr(insn, 'dispatch_runtime', False)
                    and not getattr(insn, 'return_trampoline', False))
                or direct_call or direct_long_tail(insn, ops))
            if not supported:
                raise ValueError(f"instruction timing does not yet support {insn.mnem} at {insn.addr:06X}")
            returns += insn.mnem in ("RTS", "RTL") or direct_long_tail(insn, ops)
    if not returns:
        raise ValueError("instruction timing requires a reachable RTS, RTL or direct JML")
    # Track bytes in local stack brackets, including X0 word saves.
    # Equal depth at joins rejects loops that accumulate or consume frames.
    depths = {cfg.entry: 0}
    pending = [cfg.entry]
    while pending:
        key = pending.pop()
        depth = depths[key]
        for insn, ops in block_pairs[key]:
            if insn.mnem == "PHA":
                depth += 1 if insn.m_flag else 2
            elif insn.mnem == "PLA":
                depth -= 1 if insn.m_flag else 2
            elif insn.mnem in ("PHX", "PHY"):
                depth += 2
            elif insn.mnem in ("PLX", "PLY"):
                depth -= 2
            if depth < 0:
                raise ValueError("instruction timing cannot pull the caller's frame")
            if depth and (insn.mnem in ("RTS", "RTL", "JSR", "JSL")
                          or direct_long_tail(insn, ops)):
                raise ValueError("instruction timing requires balanced stack at transfers")
        for successor in cfg.blocks[key].successors:
            if successor in depths:
                if depths[successor] != depth:
                    raise ValueError("instruction timing requires equal stack depth at joins")
            else:
                depths[successor] = depth
                pending.append(successor)
    return depths


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
        if getattr(self, "linear_word_read", False):
            line = line.replace("cpu_aot_insn_read16(", "cpu_aot_insn_read16_linear(")
        if getattr(self, "reverse_word_write", False):
            line = line.replace("cpu_aot_insn_write16(",
                                "cpu_aot_insn_write16_reverse(")
        line = line.replace("cpu->cycles +=", "_aot_timing.cycles +=")
        line = line.replace("cpu->master_cycles +=", "_aot_timing.master +=")
        list.append(self, line)

    def instruction(self, insn, cycles):
        self.pc = insn.addr & 0xFFFFFF
        self.linear_word_read = linear_word_read(insn)
        # Selected word RMW and register saves write high byte first.
        # Keep ordinary stores and unselected generation unchanged.
        self.reverse_word_write = (
            (insn.m_flag == 0 and (insn.opcode in (0x26, 0x46, 0xC6)
                                  or insn.mnem == "PHA"))
            or (insn.x_flag == 0 and insn.mnem in ("PHX", "PHY")))
        self.extend([
            "if (interp_bridge_lle_instruction_boundary_reached(cpu)) {",
            f"  return interp_bridge_lle_yield_unwind(cpu, 0x{self.pc:06X}u);",
            "}",
            "cpu->coprocessor_master_cycles = cpu->master_cycles;",
            f"_aot_timing = (CpuAotInstructionTiming){{{cycles}, {cycles * 6}}};",
            f"cpu_aot_insn_bus_extra(&_aot_timing, {self.pc >> 16}, "
            f"0x{self.pc & 0xFFFF:04X}, {insn.length});",
        ])

    def commit(self):
        return f"cpu_aot_insn_commit(cpu, &_aot_timing, 0x{self.pc:06X}u, {self.audit});"
