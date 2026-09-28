# Declared callee exit widths

Use `exit_mx_for <pc24> <entry_m> <entry_x> <exit_m> <exit_x>` to declare
one exact callee entry and its return widths. Addresses are hexadecimal;
width flags are decimal `0` or `1`. A set flag selects eight-bit registers.

For example, this synthetic declaration applies only to entry M1X0:

```text
exit_mx_for 018200 1 0 0 0
```

The Python and native whole-program analyzers also apply that exact entry
contract to a valid LoROM mirror, under their existing mapping rule. They do
not create exit facts for other entry widths. The declaration is independent
of `func` and `force_lle`: an interpreted callee can have a known exit without
becoming an AOT body. Unknown entry variants keep their existing fallback.

Exact declarations take precedence over the legacy address-wide `exit_mx_at`
directive and inferred exits. Keep explicit facts separate from inferred
routes so analysis refreshes cannot erase them. The legacy regeneration path
also preserves exact declarations across auto-promotion and exit inference.
Changing the cfg invalidates the normal emitted-output cache.

Use evidence or sound static analysis to establish the contract. Observing
one entry width does not support other widths. A declaration does not prove
that a routine returns on every path, and it does not validate compiled timing.
Do not use it to hide an unresolved return convention or force compilation.
The legacy `exit_mx_at <pc24> <exit_m> <exit_x>` keeps its address-wide behavior.

## Analyze code that remains interpreted

`interpret_only <pc24>` is an emission policy for the manifest-driven pipeline.
Reachable code and its dependencies still participate in the normal exit
solver. The named address and its valid LoROM mirror have no AOT body at any
entry width. This does not add a root, assert an exit width, or bypass an
unresolved path. Exit proofs remain keyed by exact entry M/X.

Use `force_lle` when analysis itself must stop at the boundary, for example
when the callable boundary is unsafe to decode. It takes precedence if both
directives name an address. Use `interpret_only` when the code is suitable for
analysis but its generated execution has not passed validation. Apply it to
each unvalidated dependency as well: it does not exclude an entire call tree.

Both analyzers retain the same proof rules. The common emitter applies the
selection after analysis, including when it consumes a native manifest.
An analysis manifest can therefore mark an interpreted node `aot_eligible`;
that means the analysis succeeded, not that a body was emitted. Read the
emitter count or dispatch table to measure actual AOT coverage. An explicit
emission exclusion also takes precedence over an HLE annotation.

This directive is supported by `v2_emit.py`, not the legacy `v2_regen.py`
emission path.
