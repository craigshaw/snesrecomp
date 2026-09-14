/* ROM-free alignment contract. Compile as both C and C++: ppu.h is shared
 * by the C renderer and C++ hosts. ClearBackdrop writes eight bytes at a time. */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "snes/ppu.h"

/* A leading byte catches declarations that align only a typedef name rather
 * than the struct itself. Check the struct tag as well as the typedef. */
typedef struct AlignmentProbe {
    char prefix;
    struct PpuPixelPrioBufs buffers[2];
} AlignmentProbe;

#define CHECK_LAYOUT(name, condition) typedef char name[(condition) ? 1 : -1]
CHECK_LAYOUT(buffer_alignment, offsetof(AlignmentProbe, buffers) % 8 == 0);
CHECK_LAYOUT(buffer_size, sizeof(PpuPixelPrioBufs) == kPpuBufWidth * sizeof(PpuZbufType));
CHECK_LAYOUT(buffer_stride, sizeof(PpuPixelPrioBufs) % 8 == 0);
CHECK_LAYOUT(data_offset, offsetof(PpuPixelPrioBufs, data) == 0);
CHECK_LAYOUT(bg_offset, offsetof(Ppu, bgBuffers) % 8 == 0);
CHECK_LAYOUT(obj_offset, offsetof(Ppu, objBuffer) % 8 == 0);
CHECK_LAYOUT(overlay_offset, offsetof(Ppu, overlayBuffers) % 8 == 0);
CHECK_LAYOUT(ppu_stride, sizeof(Ppu) % 8 == 0);

static int aligned(const void *address) {
    return (uintptr_t)address % 8 == 0;
}

static int check_ppu(const Ppu *ppu) {
    size_t i;
    if (!aligned(ppu->objBuffer.data)) return 0;
    for (i = 0; i < sizeof(ppu->bgBuffers) / sizeof(ppu->bgBuffers[0]); ++i)
        if (!aligned(ppu->bgBuffers[i].data)) return 0;
    for (i = 0; i < sizeof(ppu->overlayBuffers) / sizeof(ppu->overlayBuffers[0]); ++i)
        if (!aligned(ppu->overlayBuffers[i].data)) return 0;
    return 1;
}

int main(void) {
    AlignmentProbe stack;
    static Ppu global_ppu;
    Ppu *heap = (Ppu *)malloc(2 * sizeof(Ppu));
    int ok;
    if (!heap) return 1;
    ok = aligned(stack.buffers[0].data) && aligned(stack.buffers[1].data) &&
         check_ppu(&global_ppu) && check_ppu(&heap[0]) && check_ppu(&heap[1]);
    free(heap);
    if (!ok) {
        fputs("ppu_alignment_test: FAIL (unaligned priority buffer)\n", stderr);
        return 1;
    }
    puts("ppu_alignment_test: PASS");
    return 0;
}
