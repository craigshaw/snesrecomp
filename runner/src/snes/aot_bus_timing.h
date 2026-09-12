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
#endif
