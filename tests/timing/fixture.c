/* ROM-free timing differential. The shared bridge fixture supplies a flat
 * bus, dispatch stubs, and disabled peripheral scheduling. Generated bodies
 * are real emitter output, not hand-written substitutes. */
#define main bridge_contract_main
#define cpu_write8 bridge_fixture_write8
#define cpu_write16 bridge_fixture_write16
#include "../interp816/bridge_test.c"
#undef main
#undef cpu_write8
#undef cpu_write16
#include "cpu_trace.h"

typedef struct TimingWrite {
    uint32 address;
    uint8 value;
    uint64_t master;
} TimingWrite;
static TimingWrite writes[32];
static unsigned write_count;
static int recording;

void cpu_write8(CpuState *cpu, uint8 bank, uint16 address, uint8 value) {
    if (recording) {
        if (write_count >= 32) { fputs("write buffer overflow\n", stderr); exit(2); }
        writes[write_count++] = (TimingWrite){
            ((uint32)bank << 16) | address, value, cpu->master_cycles};
    }
    bridge_fixture_write8(cpu, bank, address, value);
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
    if (argc != 3) return 2;
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
    g_memsel = test->memsel;
    g_c.PB = (uint8)(test->pc >> 16);
    cpu_mirrors_to_p(&g_c);
    load(test->pc, test->code, test->size);
    /* A real RTL frame for a return to $00:9000, outside every fake entry. */
    g_c.S = 0x01FC;
    RAM[0x01FD] = 0xFF;
    RAM[0x01FE] = 0x8F;
    RAM[0x01FF] = 0;
    g_c.host_return_valid = 3;
    recording = 1;
    int ok;
    if (!strcmp(argv[2], "aot"))
        ok = test->body(&g_c) == RECOMP_RETURN_NORMAL;
    else if (!strcmp(argv[2], "interp"))
        ok = interp_bridge_run(&g_c, test->pc) == 1;
    else return 2;
    recording = 0;
    cpu_mirrors_to_p(&g_c);
    printf("{\"ok\":%d,\"a\":%u,\"x\":%u,\"y\":%u,\"s\":%u,"
           "\"p\":%u,\"d\":%u,\"db\":%u,\"m\":%u,\"xf\":%u,"
           "\"cpu_cycles\":%llu,\"master_cycles\":%llu,\"writes\":[",
           ok, g_c.A, g_c.X, g_c.Y, g_c.S, g_c.P, g_c.D, g_c.DB,
           g_c.m_flag, g_c.x_flag,
           (unsigned long long)g_c.cycles,
           (unsigned long long)g_c.master_cycles);
    for (unsigned i = 0; i < write_count; ++i)
        printf("%s{\"address\":%u,\"value\":%u,\"master\":%llu}",
               i ? "," : "", writes[i].address, writes[i].value,
               (unsigned long long)writes[i].master);
    puts("]}");
    free(RAM);
    return ok ? 0 : 2;
}
