#ifndef SNESRECOMP_AOT_BUS_TIMING_H
#define SNESRECOMP_AOT_BUS_TIMING_H

#include "../cpu_state.h"
#include "interp_bridge.h"
#include "snes_cycles.h"

/* Generated blocks precharge six clocks per CPU cycle. These helpers add
 * only bus wait states. They do not establish instruction/event precision.
 * In particular, write callbacks still observe the block's precharge. */
static inline void cpu_aot_extra(CpuState *cpu, uint32 pc, int audit,
                                 unsigned clocks) {
    if (audit && clocks) interp_bridge_event_audit_charge(cpu, pc, clocks);
    cpu->master_cycles += clocks;
}

static inline void cpu_aot_bus_extra(CpuState *cpu, uint32 pc, int audit,
                                     uint8 bank, uint16 addr, unsigned bytes) {
    unsigned clocks = 0;
    for (unsigned i = 0; i < bytes; ++i)
        clocks += (unsigned)snes_region_speed(((uint32)bank << 16) |
                    (uint16)(addr + i), g_memsel) - SNES_CYC_INTERNAL;
    cpu_aot_extra(cpu, pc, audit, clocks);
}

static inline void cpu_aot_fetch_extra(CpuState *cpu, uint32 pc,
                                       unsigned bytes, int audit) {
    cpu_aot_bus_extra(cpu, pc, audit, (uint8)(pc >> 16), (uint16)pc, bytes);
}

static inline uint8 cpu_aot_read8(CpuState *cpu, uint32 pc, int audit,
                                   uint8 bank, uint16 addr) {
    cpu_aot_bus_extra(cpu, pc, audit, bank, addr, 1);
    return cpu_read8(cpu, bank, addr);
}

static inline uint16 cpu_aot_read16(CpuState *cpu, uint32 pc, int audit,
                                     uint8 bank, uint16 addr) {
    cpu_aot_bus_extra(cpu, pc, audit, bank, addr, 2);
    return cpu_read16(cpu, bank, addr);
}

static inline void cpu_aot_write8(CpuState *cpu, uint32 pc, int audit,
                                  uint8 bank, uint16 addr, uint8 value) {
    cpu_aot_bus_extra(cpu, pc, audit, bank, addr, 1);
    cpu_write8(cpu, bank, addr, value);
}

static inline void cpu_aot_write16(CpuState *cpu, uint32 pc, int audit,
                                   uint8 bank, uint16 addr, uint16 value) {
    cpu_aot_bus_extra(cpu, pc, audit, bank, addr, 2);
    cpu_write16(cpu, bank, addr, value);
}

/* Opt-in native leaf instruction timing. Like interp_bridge_runOpcode, callbacks
 * observe the start of the instruction. This is not physical bus-edge timing.
 * Keep pending costs local so no future clock is visible to an MMIO callback. */
typedef struct CpuAotInstructionTiming {
    unsigned cycles;
    unsigned master;
} CpuAotInstructionTiming;

static inline void cpu_aot_insn_bus_extra(CpuAotInstructionTiming *timing,
                                         uint8 bank, uint16 addr, unsigned bytes) {
    for (unsigned i = 0; i < bytes; ++i)
        timing->master += (unsigned)snes_region_speed(((uint32)bank << 16) |
            (uint16)(addr + i), g_memsel) - SNES_CYC_INTERNAL;
}

static inline uint8 cpu_aot_insn_read8(CpuState *cpu, CpuAotInstructionTiming *timing,
                                      uint8 bank, uint16 addr) {
    cpu_aot_insn_bus_extra(timing, bank, addr, 1);
    int apu = !(bank & 0x40) && addr >= 0x2140 && addr <= 0x217F;
    int apu_saved = apu ? interp_bridge_aot_apu_begin(cpu) : 0;
    int saved = g_aot_instruction_read_active;
    g_aot_instruction_read_active = 1;
    uint8 value = cpu_read8(cpu, bank, addr);
    g_aot_instruction_read_active = saved;
    if (apu) interp_bridge_aot_apu_end(apu_saved);
    return value;
}
static inline uint16 cpu_aot_insn_read16(CpuState *cpu, CpuAotInstructionTiming *timing,
                                        uint8 bank, uint16 addr) {
    cpu_aot_insn_bus_extra(timing, bank, addr, 2);
    int apu = !(bank & 0x40) && addr >= 0x2140 && addr <= 0x217F;
    int apu_saved = apu ? interp_bridge_aot_apu_begin(cpu) : 0;
    int saved = g_aot_instruction_read_active;
    g_aot_instruction_read_active = 1;
    uint16 value = cpu_read16(cpu, bank, addr);
    g_aot_instruction_read_active = saved;
    if (apu) interp_bridge_aot_apu_end(apu_saved);
    return value;
}
static inline uint16 cpu_aot_insn_read16_linear(CpuState *cpu,
                                               CpuAotInstructionTiming *timing,
                                               uint8 bank, uint16 addr) {
    /* Contiguous words retain the interpreter's atomic MMIO callback. */
    if (addr != 0xFFFF) return cpu_aot_insn_read16(cpu, timing, bank, addr);
    uint32 next = ((((uint32)bank << 16) | addr) + 1u) & 0xFFFFFFu;
    uint16 low = cpu_aot_insn_read8(cpu, timing, bank, addr);
    return low | ((uint16)cpu_aot_insn_read8(cpu, timing, (uint8)(next >> 16),
                                          (uint16)next) << 8);
}
static inline void cpu_aot_insn_write8(CpuState *cpu, CpuAotInstructionTiming *timing,
                                      uint8 bank, uint16 addr, uint8 value) {
    cpu_aot_insn_bus_extra(timing, bank, addr, 1);
    int apu = !(bank & 0x40) && addr >= 0x2140 && addr <= 0x217F;
    int apu_saved = apu ? interp_bridge_aot_apu_begin(cpu) : 0;
    cpu_write8(cpu, bank, addr, value);
    if (apu) interp_bridge_aot_apu_end(apu_saved);
}
static inline void cpu_aot_insn_write16(CpuState *cpu, CpuAotInstructionTiming *timing,
                                       uint8 bank, uint16 addr, uint16 value) {
    cpu_aot_insn_bus_extra(timing, bank, addr, 2);
    int apu = !(bank & 0x40) && addr >= 0x2140 && addr <= 0x217F;
    int apu_saved = apu ? interp_bridge_aot_apu_begin(cpu) : 0;
    cpu_write16(cpu, bank, addr, value);
    if (apu) interp_bridge_aot_apu_end(apu_saved);
}
static inline void cpu_aot_insn_write16_reverse(CpuState *cpu,
                                               CpuAotInstructionTiming *timing,
                                               uint8 bank, uint16 addr, uint16 value) {
    cpu_aot_insn_write8(cpu, timing, bank, (uint16)(addr + 1), (uint8)(value >> 8));
    cpu_aot_insn_write8(cpu, timing, bank, addr, (uint8)value);
}
static inline void cpu_aot_insn_commit(CpuState *cpu, CpuAotInstructionTiming *timing,
                                      uint32 pc, int audit) {
    if (audit) interp_bridge_event_audit_charge(cpu, pc, timing->master);
    interp_bridge_commit_instruction(cpu, timing->cycles, timing->master);
}
#endif
