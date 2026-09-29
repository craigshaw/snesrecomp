"""Opt-in scheduler continuations, separate from architectural function roots."""
import os
import re
from dataclasses import fields, is_dataclass

from v2.ir import Value
from snes65816 import ABS, LONG

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


def interpreted_tails(block_pairs, cfg):
    """Select known exact JML targets that use the owning interpreter handoff.

    Local saves can remain on the guest stack at this transfer. No compiled
    callee or target exit-state contract is inferred from that stack depth.
    """
    from v2.codegen import get_name_for_pc, has_exact_variant
    result = set()
    for key, pairs in block_pairs.items():
        if not pairs or cfg.blocks[key].successors:
            continue
        insn = pairs[-1][0]
        if insn.mnem != 'JMP' or insn.mode != LONG:
            continue
        target = insn.operand & 0xFFFFFF
        if (get_name_for_pc(target) is not None
                and not has_exact_variant(target, insn.m_flag, insn.x_flag)):
            result.add((insn.addr, insn.m_flag, insn.x_flag))
    return result


def internal_interpreted_backedges(block_pairs, cfg, instruction_depths):
    """Prove unnamed internal JML landings without inventing function roots.

    The ordinary stack check runs first, so these transfers cannot borrow the
    saved-stack exception for named interpreted tails. Require zero local depth
    at both exact instruction boundaries. Leave the destination interpreted so
    its call/return and progress history stay with the active scheduler owner.
    """
    from v2.codegen import get_name_for_pc, has_exact_variant
    result = set()
    entry = (cfg.entry.pc, cfg.entry.m, cfg.entry.x)
    for key, pairs in block_pairs.items():
        if not pairs or cfg.blocks[key].successors:
            continue
        insn = pairs[-1][0]
        if insn.mnem != 'JMP' or insn.mode != LONG:
            continue
        target = (insn.operand & 0xFFFFFF, insn.m_flag, insn.x_flag)
        site = (insn.addr, insn.m_flag, insn.x_flag)
        if (target != entry and target[0] >> 16 == insn.addr >> 16
                and target[0] < insn.addr and get_name_for_pc(target[0]) is None
                and not has_exact_variant(*target)
                and instruction_depths.get(site) == 0
                and instruction_depths.get(target) == 0):
            result.add(site)
    return result


def validate(points, block_pairs, depths, graph, entry_s_offset,
             interpreted_tail_keys=()):
    """Require a proven local stack depth and no predecessor host temporaries."""
    if entry_s_offset or graph.const_z_folds:
        raise ValueError("continuations require plain stack entries and no folded branches")
    keys = {(k.pc, k.m, k.x): k for k in block_pairs}
    for point in points:
        depth = depths.get(keys.get(point))
        if depth is None or not 0 <= depth < 0x10000:
            raise ValueError(f"continuation {point} is not a proven exact block entry")
    for pairs in block_pairs.values():
        defined = set()
        for insn, ops in pairs:
            # Only a known interpreted JML uses the existing owner handoff.
            # Compiled and unresolved external tails still need separate proof.
            if (insn.mnem == "JMP" and insn.mode != ABS
                    and (insn.addr, insn.m_flag, insn.x_flag) not in interpreted_tail_keys):
                raise ValueError("continuations require known interpreted JMP tails")
            for op in ops:
                for field in fields(op):
                    value = getattr(op, field.name)
                    if field.name != "out" and any(v not in defined for v in _values(value)):
                        raise ValueError("continuations cannot use a predecessor's temporary")
                out = getattr(op, "out", None)
                if isinstance(out, Value):
                    defined.add(out)
    return {point: depths[keys[point]] for point in points}
