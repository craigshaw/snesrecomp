"""Experimental generated bus-clock accounting, enabled at generation time.

Every CPU cycle starts at six master clocks. Add only the wait-state excess
for instruction fetches and emitted architectural data/stack accesses. Keep
host prologue inspection reads outside this transformation.
"""
import os
import re


def enabled():
    return bool(os.environ.get("SNESRECOMP_EMIT_BUS_TIMING"))


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
