#include "input_replay.h"

#include "crc32.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
  kHeaderSize = 192,
  kSampleSize = 24,
  kTrailerSize = 40,
};

static const uint8_t kMagic[8] = {'S', 'N', 'R', 'P', 'L', 'Y', 0x1a, '\n'};
static const uint8_t kTrailerMagic[8] = {'S', 'N', 'R', 'D', 'O', 'N', 'E', '\n'};

static uint16_t read_le16(const uint8_t *p) {
  return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t read_le32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t read_le64(const uint8_t *p) {
  return read_le32(p) | ((uint64_t)read_le32(p + 4) << 32);
}

static bool all_zero(const uint8_t *data, size_t size) {
  for (size_t i = 0; i < size; ++i) {
    if (data[i] != 0)
      return false;
  }
  return true;
}

static bool valid_fixed_ascii(const uint8_t *data, size_t size) {
  size_t end = 0;
  while (end < size && data[end] != 0) {
    if (data[end] < 0x20 || data[end] > 0x7e)
      return false;
    ++end;
  }
  if (end == 0 || end == size)
    return false;
  return all_zero(data + end, size - end);
}

static bool valid_mask(uint16_t mask) {
  return (mask & 0xf000u) == 0 && (mask & 0x030u) != 0x030u &&
         (mask & 0x0c0u) != 0x0c0u;
}

static bool fail(SnesInputReplay *replay, const char *message) {
  snprintf(replay->error, sizeof(replay->error), "%s", message);
  free(replay->data);
  replay->data = NULL;
  replay->size = 0;
  replay->sample_count = 0;
  replay->next_sample = 0;
  return false;
}

bool snes_input_replay_open(SnesInputReplay *replay, const char *path,
                            const uint8_t expected_rom_sha256[32]) {
  FILE *file;
  long length;
  size_t payload_size;
  const uint8_t *trailer;
  uint32_t count;
  uint64_t previous_clock = 0;

  if (!replay)
    return false;
  memset(replay, 0, sizeof(*replay));
  if (!path || !path[0])
    return fail(replay, "replay path is empty");
  file = fopen(path, "rb");
  if (!file)
    return fail(replay, "unable to open replay");
  if (fseek(file, 0, SEEK_END) != 0 || (length = ftell(file)) < 0 ||
      fseek(file, 0, SEEK_SET) != 0) {
    fclose(file);
    return fail(replay, "unable to measure replay");
  }
  if ((long)(size_t)length != length) {
    fclose(file);
    return fail(replay, "replay is too large for this host");
  }
  replay->size = (size_t)length;
  replay->data = (uint8_t *)malloc(replay->size ? replay->size : 1);
  if (!replay->data ||
      fread(replay->data, 1, replay->size, file) != replay->size) {
    fclose(file);
    return fail(replay, "unable to read complete replay");
  }
  if (fclose(file) != 0)
    return fail(replay, "unable to close replay after reading");
  if (replay->size < kHeaderSize + kTrailerSize)
    return fail(replay, "replay is too short or incomplete");
  if (memcmp(replay->data, kMagic, sizeof(kMagic)) != 0)
    return fail(replay, "replay magic is invalid");
  if (read_le16(replay->data + 8) != 1 ||
      read_le16(replay->data + 10) != kHeaderSize ||
      read_le16(replay->data + 12) != kSampleSize)
    return fail(replay, "replay version or record sizes are unsupported");
  if (replay->data[14] != 2 || replay->data[15] != 1 ||
      read_le32(replay->data + 16) != 3)
    return fail(replay, "replay controller, timing, or start contract is unsupported");
  if (all_zero(replay->data + 20, 16) || all_zero(replay->data + 36, 32))
    return fail(replay, "replay recording or ROM identity is empty");
  if (!valid_fixed_ascii(replay->data + 108, 24) ||
      !valid_fixed_ascii(replay->data + 132, 24) ||
      !valid_fixed_ascii(replay->data + 156, 16) ||
      !all_zero(replay->data + 172, 20))
    return fail(replay, "replay producer metadata or reserved bytes are invalid");
  memcpy(replay->rom_sha256, replay->data + 36, 32);
  if (expected_rom_sha256 &&
      memcmp(replay->rom_sha256, expected_rom_sha256, 32) != 0)
    return fail(replay, "replay ROM SHA-256 does not match the loaded ROM");

  payload_size = replay->size - kTrailerSize;
  trailer = replay->data + payload_size;
  if (memcmp(trailer, kTrailerMagic, sizeof(kTrailerMagic)) != 0)
    return fail(replay, "replay is incomplete");
  count = read_le32(trailer + 8);
#if SIZE_MAX <= UINT32_MAX
  if (count > (SIZE_MAX - kHeaderSize - kTrailerSize) / kSampleSize)
    return fail(replay, "replay sample count is too large for this host");
#endif
  if (replay->size != kHeaderSize + (size_t)count * kSampleSize + kTrailerSize)
    return fail(replay, "replay length does not match its sample count");
  if (read_le16(trailer + 12) < 1 || read_le16(trailer + 12) > 3 ||
      read_le16(trailer + 14) != 0 || !all_zero(trailer + 32, 8))
    return fail(replay, "replay completion record is unsupported");
  if (crc32_compute(replay->data, payload_size) != read_le32(trailer + 16))
    return fail(replay, "replay payload CRC32 mismatch");

  for (uint32_t i = 0; i < count; ++i) {
    const uint8_t *record = replay->data + kHeaderSize + (size_t)i * kSampleSize;
    uint64_t clock = read_le64(record + 8);
    uint16_t port1 = read_le16(record + 16);
    uint16_t port2 = read_le16(record + 18);
    if (read_le32(record) != i || read_le32(record + 4) != i + 1)
      return fail(replay, "replay sample anchors are not contiguous");
    if ((i != 0 && clock <= previous_clock) || !valid_mask(port1) ||
        !valid_mask(port2) || read_le32(record + 20) != 0)
      return fail(replay, "replay sample state is invalid");
    previous_clock = clock;
  }
  if (read_le32(trailer + 20) != count ||
      (count != 0 && read_le64(trailer + 24) < previous_clock))
    return fail(replay, "replay completion anchors do not match its samples");

  replay->sample_count = count;
  replay->error[0] = '\0';
  return true;
}

bool snes_input_replay_next(SnesInputReplay *replay,
                            SnesInputReplaySample *sample,
                            uint32_t *packed_inputs) {
  const uint8_t *record;
  SnesInputReplaySample value;
  if (!replay || !replay->data || replay->next_sample >= replay->sample_count)
    return false;
  record = replay->data + kHeaderSize + (size_t)replay->next_sample * kSampleSize;
  value.poll = read_le32(record);
  value.completed_frame = read_le32(record + 4);
  value.master_clock = read_le64(record + 8);
  value.port1 = read_le16(record + 16);
  value.port2 = read_le16(record + 18);
  if (sample)
    *sample = value;
  if (packed_inputs)
    *packed_inputs = value.port1 | ((uint32_t)value.port2 << 12);
  ++replay->next_sample;
  return true;
}

bool snes_input_replay_finished(const SnesInputReplay *replay) {
  return replay && replay->data && replay->next_sample == replay->sample_count;
}

const char *snes_input_replay_error(const SnesInputReplay *replay) {
  return replay && replay->error[0] ? replay->error : NULL;
}

void snes_input_replay_close(SnesInputReplay *replay) {
  if (!replay)
    return;
  free(replay->data);
  memset(replay, 0, sizeof(*replay));
}
