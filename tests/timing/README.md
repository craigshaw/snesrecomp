# Generated/interpreted timing differential

Run from the snesrecomp root with Python 3 and a POSIX C compiler:

```sh
python3 tests/timing/run.py --out-dir build/timing-check
```

The output directory must be new. Omit `--out-dir` to use a temporary directory
that is removed at exit. `--cc clang` or `CC` selects the compiler. Windows
MSVC command-line support has not been implemented or tested.

The runner emits real C from synthetic 65816 bytes, compiles it with the real
interpreter bridge, and runs each case in each tier in a separate process.
It checks CPU registers, flags, stack restoration, and write addresses/values.
It separately compares CPU cycle totals, master clocks, and the clock reported
at each write callback. It saves generated fixture code, the compiler command,
build and execution logs, and `results.json`. No game ROM or profile is needed.

Exit codes:

- `0`: all checks, including timing parity, pass.
- `1`: state and interpreter-budget checks pass, but timing differs.
- `2`: a tool, fixture, interpreter-budget or architectural check fails.

This is a diagnostic suite with known timing failures at integration revision
`3678d0a6d7036217f26e32f6b9087d2933783690`. It is separate from the existing
passing Python and C suites. Do not treat a successful compile or a matching
register result as a timing pass.

## Independently calculated budgets

Each case runs in native mode with X=16-bit. M is 8-bit except for `lda16_rtl`.
Low WRAM transfers cost eight master clocks, MMIO transfers cost six, internal
cycles cost six, and instruction fetches cost eight in SlowROM or six in
enabled FastROM. These budgets check the bridge's accounting model. They are
not an independent hardware measurement.

SlowROM RTL has four bus reads (opcode and three stack bytes) and two internal
cycles, giving `4*8 + 2*6 = 44` master clocks. FastROM RTL fetches its opcode
in six clocks but still reads the low-WRAM stack at eight clocks, giving 42.
For example, `LDA #0; STA $4200; RTL` costs `16 + 30 + 44 = 90` master clocks.
The current emitter charges its 12 CPU cycles at eight clocks each, giving 96.

| Case | CPU cycles | Interpreter master clocks |
| --- | ---: | ---: |
| NOP; RTL | 8 | 58 |
| LDA immediate 8-bit; RTL | 8 | 60 |
| LDA immediate 16-bit; RTL | 9 | 68 |
| LDA immediate; WRAM store; RTL | 12 | 92 |
| LDA immediate; MMIO store; RTL | 12 | 90 |
| NOP; LDA immediate; MMIO store; RTL | 14 | 104 |
| FastROM LDA immediate; RTL | 8 | 54 |
| FastROM LDA immediate; WRAM store; RTL | 12 | 80 |
| FastROM LDA immediate; MMIO store; RTL | 12 | 78 |
| Folded taken branch | 11 | 82 |
| Folded untaken branch with NOP fall-through | 12 | 90 |
| Memory load; dynamic taken branch | 13 | 98 |
| Memory load; dynamic untaken branch with NOP fall-through | 14 | 106 |
| Indexed WRAM read across page boundary; RTL | 11 | 82 |

The branch cases distinguish constant-Z folding from a runtime condition.
At the pinned integration revision, the folded taken branch loses its extra
CPU cycle: AOT reports 10, while the interpreter reports 11. The dynamic
taken branch retains it. The following branch-cycle fix restores the folded
case to 11 CPU cycles; master-clock parity remains unresolved.

Run the focused executable regression with:

```sh
python3 tests/timing/test_branch_cycles.py
```

It checks 96 combinations of LDA/LDX/LDY immediate, 8/16-bit load width,
BEQ/BNE, taken/untaken, zero/nonzero displacement, and SlowROM/FastROM. Each
tier must match the independently calculated CPU count and the other tier's
checked architectural state. This test is included in `tests/run_c_tests.sh`,
which therefore requires Python 3 as well as a C compiler. Passing this
focused test does not imply that the full timing differential passes.

## Experimental bus-clock accounting

After the indexed-cycle and bus-clock patches, run:

```sh
python3 tests/timing/run.py --bus-timing --out-dir build/timing-bus-check
python3 tests/timing/test_bus_clocks.py
```

The first command still returns 1: all 14 CPU/master totals agree, but five
write-timestamp comparisons fail. The second checks final clocks, registers,
and final written bytes in 376 cases and returns 0. It reports 31 differing
byte-write sequences separately. Final memory agreement does not establish
equivalent MMIO side effects, byte-write order, or write timestamps.

Coverage includes direct/absolute/long/indirect reads and writes, 8/16-bit
operand widths, indexed writes and read-modify-write, word accesses across
speed-region boundaries, a nonzero direct-page low byte, stack operations,
ordinary JSR/JSL calls and RTS/RTL/RTI returns, and multi-byte MVN/MVP.
The fixture supports ordinary paired calls. It does not model arbitrary
manual return-frame manipulation or validate all optimised call/dispatch
trampolines and omitted stack operations.

For actual generation, `SNESRECOMP_EMIT_BUS_TIMING=1` enables the experimental
path. The option participates in the generation-cache key, together with the
shared instruction/cycle model sources. With the option absent, generated
code retains block costs at code-region speed. The indexed-write cycle
corrections apply independently of this option in the generator/interpreter.

Enabled generation precharges six master clocks for each CPU cycle and adds
the excess cost for each instruction fetch and emitted data/stack access.
Host return-frame inspection reads remain uncharged. Indexed long-indirect
pointer reads are evaluated once, and RTI's discarded return bytes still
contribute their access costs. Runtime-only internal cycles cost six clocks.
MEMSEL is sampled at each fetch/access; no extra guest memory reads are made
solely to calculate timing.

This is an accounting experiment. Blocks still precharge their base cost,
and no new per-instruction event/yield policy is introduced. Keep it out of
normal title generation until the title comparisons pass. Correct totals in
these synthetic cases are not a claim of complete hardware bus accuracy.

The indexed write/RMW correction follows the fixed indexed-write base costs
in the [WDC W65C816S datasheet](https://www.westerndesigncenter.com/wdc/documentation/w65c816s.pdf),
Tables 3-1 and 5-4. The old interpreter added another conditional cycle to
these writes; the generator also incorrectly added page-cross cycles to RMW.

## Scope and remaining timing work

The fixture reuses the bridge suite's flat bus and disabled peripheral/event
scheduling. It does not validate DMA, refresh, raster deadlines, interrupts,
APU scheduling, or full-game behaviour. Write clocks are values visible in the
callback, not measured hardware bus edges: the bridge charges after executing
an instruction, while generated code charges before executing a whole block.

These tests isolate three concerns: bus versus internal-cycle costs,
instruction versus block write timing, and preservation of branch timing
through optimisation. After a fix, retain these tests and rerun title-level
frame/state comparisons before accepting additional AOT roots.

## Opt-in native leaf instruction timing

`SNESRECOMP_EMIT_INSTRUCTION_TIMING=008000:1:0,008100:0:1` selects exact
generated entry keys as `HEXPC:M:X`. It requires
`SNESRECOMP_EMIT_BUS_TIMING=1`. Both options participate in the output cache
key. Unselected bodies retain the previous bus-accounting mode.

This bounded implementation accepts a single straight-line block ending in
RTS or RTL. Its preceding instructions can be NOP, LDA/LDX/LDY,
STA/STX/STY/STZ with immediate, direct-page, absolute or long addressing,
including their supported indexed forms. Unsupported selected bodies fail
generation. Emulation-mode invocations use the interpreter. Calls, branches,
stack manipulation, indirect addressing, RMW, RTI and block moves are outside
this instruction-timing experiment.

Each instruction accumulates costs locally while callbacks observe the
instruction's start clock, matching the bridge convention. Return-frame
reads finish before committing the return instruction. A shared bridge
completion function advances CPU/master clocks, refresh accounting, beam and
coprocessors, and the existing APU accumulator. Each next instruction checks
the scheduler deadline and pending NMI before doing any work. An unwind
retains the exact next PC and live guest stack for interpreter resumption.

```sh
python3 tests/timing/test_instruction_timing.py
```

This test runs 143 complete leaf comparisons, including write timestamps and
byte order, and 32 real bridge scheduler exit/resume comparisons. It covers
exact deadline boundaries, delayed-enable NMI, a simulated refresh delay and
a simulated beam-triggered NMI. The event fixture also compares APU time
accumulation. These peripheral hooks are test doubles, not hardware models.
The shared C suite includes this test and checks that older timing modes
keep their existing results.

The supported instruction list is a bounded experiment, not a claim of
general MMIO equivalence. APU-port handshakes, DMA, real peripheral timing,
optimised calls and other control paths still need title-level validation.
No new generation option is enabled by default.
