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

## Scope

The fixture reuses the bridge suite's flat bus and disabled peripheral/event
scheduling. It does not validate DMA, refresh, raster deadlines, interrupts,
APU scheduling, or full-game behaviour. Write clocks are values visible in the
callback, not measured hardware bus edges: the bridge charges after executing
an instruction, while generated code charges before executing a whole block.

These tests isolate three concerns: bus versus internal-cycle costs,
instruction versus block write timing, and preservation of branch timing
through optimisation. After a fix, retain these tests and rerun title-level
frame/state comparisons before accepting additional AOT roots.
