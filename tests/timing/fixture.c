/* ROM-free timing differential. The shared bridge fixture supplies a flat
 * bus, dispatch stubs, and disabled peripheral scheduling. Generated bodies
 * are real emitter output, not hand-written substitutes. */
#define main bridge_contract_main
#define cpu_write8 bridge_fixture_write8
#define cpu_write16 bridge_fixture_write16
#define cpu_dispatch_has_entry bridge_fixture_has_entry
#define cpu_dispatch_pc_paired bridge_fixture_paired
#define snes_refresh_charge bridge_fixture_refresh
#define snes_sync_master_clock bridge_fixture_sync_master
#include "../interp816/bridge_test.c"
#undef main
#undef cpu_write8
#undef cpu_write16
#undef cpu_dispatch_has_entry
#undef cpu_dispatch_pc_paired
#undef snes_refresh_charge
#undef snes_sync_master_clock
#include "cpu_trace.h"

typedef struct TimingWrite {
    uint32 address;
    uint8 value;
    uint64_t master;
} TimingWrite;
static TimingWrite writes[32];
static unsigned write_count;
static int recording;
static uint32 event_entry;
static RecompReturn (*event_body)(CpuState *);
static uint64_t refresh_at, beam_nmi_at, last_sync;
static unsigned refresh_count, sync_count;

void snes_refresh_charge(void) {
    refresh_count++;
    if (refresh_at && g_c.master_cycles >= refresh_at) {
        g_c.master_cycles += 40;
        refresh_at = 0;
    }
}
void snes_sync_master_clock(Snes *snes, uint64_t master) {
    sync_count++;
    last_sync = master;
    if (beam_nmi_at && master >= beam_nmi_at) {
        snes->nmiPending = true;
        beam_nmi_at = 0;
    }
}

int cpu_dispatch_has_entry(CpuState *cpu, uint32 pc) {
    return event_entry ? event_body && pc == event_entry : bridge_fixture_has_entry(cpu, pc);
}
RecompReturn cpu_dispatch_pc_paired(CpuState *cpu, uint32 pc, uint8 frame_size) {
    if (event_body && pc == event_entry) {
        cpu->host_return_valid = frame_size;
        return event_body(cpu);
    }
    return bridge_fixture_paired(cpu, pc, frame_size);
}

void cpu_write8(CpuState *cpu, uint8 bank, uint16 address, uint8 value) {
    if (recording) {
        if (write_count >= 32) { fputs("write buffer overflow\n", stderr); exit(2); }
        writes[write_count++] = (TimingWrite){
            ((uint32)bank << 16) | address, value, cpu->master_cycles};
    }
    bridge_fixture_write8(cpu, bank, address, value);
    if (bank == 0 && address == 0x420D) g_memsel = value & 1;
}
void cpu_write16(CpuState *cpu, uint8 bank, uint16 address, uint16 value) {
    cpu_write8(cpu, bank, address, (uint8)value);
    cpu_write8(cpu, bank, (uint16)(address + 1), (uint8)(value >> 8));
}
void cpu_dbg_funcname(const char *name) { (void)name; }
void WatchdogCheck(void) {}
int cpu_resolve_ancestor_skip(uint16 stack) { (void)stack; return -1; }
RecompReturn cpu_dispatch_pc_from(CpuState *cpu, uint32 pc, uint16 stack,
                                  uint32 source) {
    (void)cpu; (void)stack;
    fprintf(stderr, "unexpected dispatch %06X from %06X\n", pc, source);
    exit(2);
}

/* Case table and actual generated functions are supplied by run.py. */
#include "timing_cases.inc"

int main(int argc, char **argv) {
    if (argc < 3 || argc > 5) return 2;
    unsigned index = (unsigned)strtoul(argv[1], NULL, 10);
    if (index >= sizeof(cases) / sizeof(cases[0])) return 2;
    const TimingCase *test = &cases[index];
    RAM = calloc(1, MEMSZ);
    if (!RAM) return 2;
    init_cpu();
    g_c.emulation = 0;
    g_c.m_flag = test->m;
    g_c.x_flag = test->xf;
    g_c.DB = test->db;
    g_c.X = test->x;
    g_c.Y = test->y;
    g_c.D = test->d;
    g_memsel = test->memsel;
    g_c.PB = (uint8)(test->pc >> 16);
    cpu_mirrors_to_p(&g_c);
    load(test->pc, test->code, test->size);
    for (unsigned i = 0; i < test->init_count; ++i)
        RAM[test->init[i].address] = test->init[i].value;
    /* A real RTL frame for a return to $00:9000, outside every fake entry. */
    g_c.S = 0x01FC;
    RAM[0x01FD] = 0xFF;
    RAM[0x01FE] = 0x8F;
    RAM[0x01FF] = 0;
    g_c.host_return_valid = 3;
    if (test->code[test->size - 1] == 0x60) {
        g_c.S = 0x01FD;
        RAM[0x01FE] = 0xFF;
        RAM[0x01FF] = 0x8F;
        g_c.host_return_valid = 2;
    }
    int interrupt = test->code[test->size - 1] == 0x40;
    if (interrupt) {
        g_c.S = 0x01FB;
        RAM[0x01FC] = g_c.P;
        RAM[0x01FD] = 0;
        RAM[0x01FE] = 0x90;
        g_c.host_return_valid = 0;
    }
    recording = 1;
    int ok;
    if (argc >= 4) {
        event_entry = test->pc;
        if (!strcmp(argv[2], "event-aot")) event_body = test->body;
        else if (strcmp(argv[2], "event-interp")) return 2;
        g_c.S = 0x01FF;
        const uint8 scheduler[] = {0x22, test->pc, test->pc >> 8, test->pc >> 16,
                                   0xA9, 0x5A, 0xCB};
        load(0x7000, scheduler, sizeof scheduler);
        if (!strcmp(argv[3], "nmi")) {
            g_test_snes.inNmi = true;
            g_test_snes.inVblank = true;
        } else if (!strcmp(argv[3], "refresh")) {
            refresh_at = 90;
            interp_bridge_set_master_deadline(120);
        } else if (!strcmp(argv[3], "beam-nmi")) {
            beam_nmi_at = 90;
        } else {
            interp_bridge_set_master_deadline(strtoull(argv[3], NULL, 10));
        }
        ok = interp_bridge_run_until_quiescent(&g_c, 0x007000) == 1;
        if (argc == 5 && !strcmp(argv[4], "resume")) {
            const uint32 resume = interp_bridge_lle_resume_pc();
            interp_bridge_set_master_deadline(1000);
            g_test_snes.nmiPending = false;
            ok = ok && interp_bridge_run_until_quiescent(&g_c, resume) == 1;
        }
    } else if (!strcmp(argv[2], "aot"))
        ok = test->body(&g_c) == RECOMP_RETURN_NORMAL;
    else if (!strcmp(argv[2], "interp"))
        ok = (interrupt ? interp_bridge_run_interrupt(&g_c, test->pc)
                        : interp_bridge_run(&g_c, test->pc)) == 1;
    else return 2;
    recording = 0;
    cpu_mirrors_to_p(&g_c);
    printf("{\"ok\":%d,\"a\":%u,\"x\":%u,\"y\":%u,\"s\":%u,"
           "\"p\":%u,\"d\":%u,\"db\":%u,\"m\":%u,\"xf\":%u,"
           "\"cpu_cycles\":%llu,\"master_cycles\":%llu",
           ok, g_c.A, g_c.X, g_c.Y, g_c.S, g_c.P, g_c.D, g_c.DB,
           g_c.m_flag, g_c.x_flag,
           (unsigned long long)g_c.cycles,
           (unsigned long long)g_c.master_cycles);
    if (test->instruction_timing) printf(
           ",\"resume\":%u,\"nmi\":%d,\"scope_depth\":%d,\"scope_underflow\":%d,"
           "\"refresh_count\":%u,\"sync_count\":%u,\"last_sync\":%llu,"
           "\"event_apu_catchup\":%.17g",
           interp_bridge_lle_resume_pc(), g_test_snes.nmiPending,
           g_push_depth, g_pop_underflow, refresh_count, sync_count,
           (unsigned long long)last_sync,
           event_entry ? g_snes->apuCatchupCycles : 0.0);
    printf(",\"writes\":[");
    for (unsigned i = 0; i < write_count; ++i)
        printf("%s{\"address\":%u,\"value\":%u,\"master\":%llu}",
               i ? "," : "", writes[i].address, writes[i].value,
               (unsigned long long)writes[i].master);
    puts("]}");
    free(RAM);
    return ok ? 0 : 2;
}
