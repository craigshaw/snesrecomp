"""Opt-in scheduler continuations, separate from architectural function roots."""
import os
import re
from dataclasses import fields, is_dataclass

from v2.ir import Value

ENV = "SNESRECOMP_EMIT_CONTINUATIONS"


def selections():
    """Map exact root keys to exact internal block keys (ROOT:M:X>PC:M:X)."""
    result = {}
    owners = {}
    for item in filter(None, os.environ.get(ENV, "").split(",")):
        if not re.fullmatch(r"[0-9a-fA-F]{6}:[01]:[01]>[0-9a-fA-F]{6}:[01]:[01]", item):
            raise ValueError(f"{ENV}: invalid selection {item!r}")
        root, point = ((int(p[0], 16), int(p[1]), int(p[2]))
                       for p in (part.split(":") for part in item.split(">")))
        if point in owners:
            raise ValueError(f"{ENV}: duplicate continuation {item!r}")
        if (root == point or root[0] >> 16 != point[0] >> 16
                or root[0] >> 16 in (0x7E, 0x7F)
                or min(root[0] & 0xFFFF, point[0] & 0xFFFF) < 0x8000):
            raise ValueError(f"{ENV}: requires an internal ROM block in the root bank")
        owners[point] = root
        result.setdefault(root, set()).add(point)
    return result


def suffix(point):
    pc, m, x = point
    return f"_resume_{pc:06X}_M{m}X{x}"


def _values(value):
    if isinstance(value, Value):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _values(item)
    elif is_dataclass(value):
        for field in fields(value):
            yield from _values(getattr(value, field.name))


def validate(points, block_pairs, depths, graph, entry_s_offset):
    """Require balanced block entries and no host temporaries from a predecessor."""
    if entry_s_offset or graph.const_z_folds:
        raise ValueError("continuations require plain stack entries and no folded branches")
    keys = {(k.pc, k.m, k.x): k for k in block_pairs}
    for point in points:
        if point not in keys or depths.get(keys[point]) != 0:
            raise ValueError(f"continuation {point} is not a balanced exact block entry")
    for pairs in block_pairs.values():
        defined = set()
        for insn, ops in pairs:
            # External tail obligations need a separate continuation contract.
            if insn.mnem == "JMP":
                raise ValueError("continuations do not yet support JMP tails")
            for op in ops:
                for field in fields(op):
                    value = getattr(op, field.name)
                    if field.name != "out" and any(v not in defined for v in _values(value)):
                        raise ValueError("continuations cannot use a predecessor's temporary")
                out = getattr(op, "out", None)
                if isinstance(out, Value):
                    defined.add(out)
