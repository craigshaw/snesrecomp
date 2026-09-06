#include <stdio.h>

#include "snes_regs.h"

static int check(int condition, const char *message) {
  if (condition)
    return 0;
  fprintf(stderr, "FAIL: %s\n", message);
  return 1;
}

int main(void) {
  int failures = 0;
  failures += check(snes_rdnmi_merge_open_bus(0x02, 0x40) == 0x42,
                    "RDNMI preserves open-bus bit 6");
  failures += check(snes_rdnmi_merge_open_bus(0x82, 0x70) == 0xf2,
                    "RDNMI combines NMI, version and open-bus bits");
  failures += check(snes_rdnmi_merge_open_bus(0xff, 0x00) == 0x8f,
                    "RDNMI never leaks driven bits 4-6");
  failures += check(snes_nmitimen_requests_nmi(0, 1, 1, 0, 0x80),
                    "enabling NMI with RDNMI latched requests NMI");
  failures += check(!snes_nmitimen_requests_nmi(1, 1, 1, 0, 0x80),
                    "writing an already-enabled NMI bit has no new edge");
  failures += check(!snes_nmitimen_requests_nmi(0, 1, 0, 0, 0x80),
                    "enabling NMI without an RDNMI latch does not request NMI");
  failures += check(!snes_nmitimen_requests_nmi(0, 0, 1, 0, 0x80),
                    "an old RDNMI latch outside VBlank does not request NMI");
  failures += check(!snes_nmitimen_requests_nmi(0, 1, 1, 1, 0x80),
                    "one VBlank cannot raise NMI twice");
  failures += check(!snes_nmitimen_requests_nmi(0, 1, 1, 0, 0x01),
                    "leaving NMI disabled does not request NMI");
  if (!failures)
    puts("rdnmi_open_bus_test: ok");
  return failures != 0;
}
