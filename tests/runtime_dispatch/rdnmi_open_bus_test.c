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
  if (!failures)
    puts("rdnmi_open_bus_test: ok");
  return failures != 0;
}
