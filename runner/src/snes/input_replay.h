#ifndef SNES_INPUT_REPLAY_H
#define SNES_INPUT_REPLAY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct SnesInputReplaySample {
  uint32_t poll;
  uint32_t completed_frame;
  uint64_t master_clock;
  uint16_t port1;
  uint16_t port2;
} SnesInputReplaySample;

typedef struct SnesInputReplay {
  uint8_t *data;
  size_t size;
  uint32_t sample_count;
  uint32_t next_sample;
  uint8_t rom_sha256[32];
  char error[192];
} SnesInputReplay;

/* Validate the complete replay before playback. expected_rom_sha256 may be NULL. */
bool snes_input_replay_open(SnesInputReplay *replay, const char *path,
                            const uint8_t expected_rom_sha256[32]);
bool snes_input_replay_next(SnesInputReplay *replay,
                            SnesInputReplaySample *sample,
                            uint32_t *packed_inputs);
bool snes_input_replay_finished(const SnesInputReplay *replay);
const char *snes_input_replay_error(const SnesInputReplay *replay);
void snes_input_replay_close(SnesInputReplay *replay);

#endif
