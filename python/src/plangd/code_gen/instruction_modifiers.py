"""Extract ordered instruction modifier slots from the normalized PTX spec."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ptx_frontend.spec.database import load_codegen_database_from_files
from ptx_frontend.spec.model import ModifierPresence, modifier_spellings


@dataclass(frozen=True, order=True)
class ModifierSlot:
    """Source spellings of one slot and whether source text may omit it."""

    spellings: tuple[str, ...]
    optional: bool


@dataclass(frozen=True)
class InstructionModifiers:
    """Shared slots and the accepted slot orders for each opcode."""

    slots: tuple[ModifierSlot, ...]
    orders_by_opcode: dict[str, tuple[tuple[int, ...], ...]]


def load_instruction_modifiers(spec_files: Iterable[Path]) -> InstructionModifiers:
    """Reuse frontend normalization for value sets, presences and alias orders."""
    database = load_codegen_database_from_files(spec_files=spec_files)
    orders_by_opcode = {}
    for instruction in database.instructions:
        orders = set()
        for variant in instruction.variants:
            active_slots = {
                slot.name: ModifierSlot(
                    tuple(sorted(set(modifier_spellings(slot)))),
                    slot.presence == ModifierPresence.OPTIONAL,
                )
                for slot in variant.modifiers
                if slot.presence != ModifierPresence.ABSENT
            }
            for order in (tuple(active_slots), *variant.modifier_order_aliases):
                slots = tuple(
                    active_slots[name] for name in order if name in active_slots
                )
                if slots:
                    orders.add(slots)
        if orders:
            orders_by_opcode[instruction.opcode] = orders

    slots = tuple(sorted({
        slot for orders in orders_by_opcode.values() for order in orders for slot in order
    }))
    slot_ids = {slot: index for index, slot in enumerate(slots)}
    return InstructionModifiers(
        slots,
        {
            opcode: tuple(sorted(
                tuple(slot_ids[slot] for slot in order) for order in orders
            ))
            for opcode, orders in sorted(orders_by_opcode.items())
        },
    )
