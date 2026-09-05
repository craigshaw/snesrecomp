#include "crc32.h"
#include "input_replay.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

enum { kHeaderSize = 192, kSampleSize = 24, kTrailerSize = 40 };

static void put16(unsigned char *p, unsigned value) {
  p[0] = (unsigned char)value;
  p[1] = (unsigned char)(value >> 8);
}

static void put32(unsigned char *p, unsigned value) {
  p[0] = (unsigned char)value;
  p[1] = (unsigned char)(value >> 8);
  p[2] = (unsigned char)(value >> 16);
  p[3] = (unsigned char)(value >> 24);
}

static void put64(unsigned char *p, unsigned long long value) {
  put32(p, (unsigned)value);
  put32(p + 4, (unsigned)(value >> 32));
}

static void put_text(unsigned char *p, size_t size, const char *value) {
  size_t length = strlen(value);
  assert(length < size);
  memcpy(p, value, length);
}

static void write_fixture(const char *path, const unsigned char rom_sha256[32]) {
  unsigned char data[kHeaderSize + 2 * kSampleSize + kTrailerSize] = {0};
  unsigned char *sample0 = data + kHeaderSize;
  unsigned char *sample1 = sample0 + kSampleSize;
  unsigned char *trailer = sample1 + kSampleSize;
  FILE *file;

  memcpy(data, "SNRPLY\x1a\n", 8);
  put16(data + 8, 1);
  put16(data + 10, kHeaderSize);
  put16(data + 12, kSampleSize);
  data[14] = 2;
  data[15] = 1;
  put32(data + 16, 3);
  for (int i = 0; i < 16; ++i)
    data[20 + i] = (unsigned char)(i + 1);
  memcpy(data + 36, rom_sha256, 32);
  put_text(data + 108, 24, "synthetic-test");
  put_text(data + 132, 24, "1.0");
  put_text(data + 156, 16, "authored");

  put32(sample0, 0);
  put32(sample0 + 4, 1);
  put64(sample0 + 8, 100);
  put16(sample0 + 16, 0);
  put16(sample0 + 18, 0);

  put32(sample1, 1);
  put32(sample1 + 4, 2);
  put64(sample1 + 8, 200);
  put16(sample1 + 16, 0x008);
  put16(sample1 + 18, 0x100);

  memcpy(trailer, "SNRDONE\n", 8);
  put32(trailer + 8, 2);
  put16(trailer + 12, 3);
  put32(trailer + 16, crc32_compute(data, kHeaderSize + 2 * kSampleSize));
  put32(trailer + 20, 2);
  put64(trailer + 24, 200);

  file = fopen(path, "wb");
  assert(file != NULL);
  assert(fwrite(data, 1, sizeof(data), file) == sizeof(data));
  assert(fclose(file) == 0);
}

int main(void) {
  const char *path = "input-replay-test.sri";
  unsigned char rom_sha256[32];
  unsigned char wrong_rom[32];
  SnesInputReplay replay = {0};
  SnesInputReplaySample sample;
  uint32_t inputs;
  FILE *file;

  memset(rom_sha256, 0x11, sizeof(rom_sha256));
  memset(wrong_rom, 0x22, sizeof(wrong_rom));
  write_fixture(path, rom_sha256);

  assert(snes_input_replay_open(&replay, path, rom_sha256));
  assert(replay.sample_count == 2);
  assert(snes_input_replay_next(&replay, &sample, &inputs));
  assert(sample.poll == 0 && sample.completed_frame == 1 && inputs == 0);
  assert(snes_input_replay_next(&replay, &sample, &inputs));
  assert(sample.master_clock == 200);
  assert(inputs == (0x008u | (0x100u << 12)));
  assert(snes_input_replay_finished(&replay));
  assert(!snes_input_replay_next(&replay, &sample, &inputs));
  snes_input_replay_close(&replay);

  assert(!snes_input_replay_open(&replay, path, wrong_rom));
  assert(strstr(snes_input_replay_error(&replay), "ROM SHA-256") != NULL);

  file = fopen(path, "r+b");
  assert(file != NULL);
  assert(fseek(file, kHeaderSize + 16, SEEK_SET) == 0);
  assert(fputc(1, file) != EOF);
  assert(fclose(file) == 0);
  assert(!snes_input_replay_open(&replay, path, rom_sha256));
  assert(strstr(snes_input_replay_error(&replay), "CRC32") != NULL);
  assert(remove(path) == 0);

  puts("input replay tests passed");
  return 0;
}
