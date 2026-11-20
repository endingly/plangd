"""Extract ordered instruction modifier slots from the normalized PTX spec."""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ptx_frontend.code_gen.emit.availability import emit_availability
from ptx_frontend.spec.database import load_codegen_database_from_files
from ptx_frontend.spec.model import (
    ModifierKind,
    ModifierPresence,
    ModifierSpec,
    modifier_spellings,
)


@dataclass(frozen=True, order=True)
class ModifierSlot:
    """Source spellings of one slot and whether source text may omit it."""

    spellings: tuple[tuple[str, int], ...]
    optional: bool


@dataclass(frozen=True)
class InstructionModifiers:
    """Shared slots and the accepted slot orders for each opcode."""

    slots: tuple[ModifierSlot, ...]
    orders_by_opcode: dict[str, tuple[tuple[tuple[int, ...], int], ...]]
    requirements: tuple[str, ...]

    @property
    def order_slot_count(self) -> int:
        """Number of slot references in the flattened order table."""
        return sum(
            len(order) for orders in self.orders_by_opcode.values() for order, _ in orders
        )


def _make_slot(
    modifier: ModifierSpec, requirement_id: Callable[[Mapping[str, object]], int]
) -> ModifierSlot:
    """Attach requirements to written values, including the true value of flags."""
    spellings = modifier_spellings(modifier)
    values = modifier.values
    if modifier.token is not None and modifier.kind == ModifierKind.FLAG:
        values = tuple(value for value in values if value.value is True)
    if values:
        choices = {
            (spelling, requirement_id(value.availability))
            for spelling, value in zip(spellings, values, strict=True)
        }
    else:
        choices = {(spelling, 0) for spelling in spellings}
    return ModifierSlot(tuple(sorted(choices)), modifier.presence == ModifierPresence.OPTIONAL)


def load_instruction_modifiers(spec_files: Iterable[Path]) -> InstructionModifiers:
    """Reuse frontend normalization and rendering for the completion tables."""
    database = load_codegen_database_from_files(spec_files=spec_files)
    requirement_ids = {"{}": 0}

    def requirement_id(requirement: Mapping[str, object]) -> int:
        """Intern equivalent normalized availability conditions."""
        key = json.dumps(requirement, sort_keys=True)
        return requirement_ids.setdefault(key, len(requirement_ids))

    orders_by_opcode = {}
    for instruction in sorted(database.instructions, key=lambda item: item.opcode):
        orders = set()
        for variant in sorted(instruction.variants, key=lambda item: item.name):
            availability = requirement_id(variant.availability)
            active_slots = {
                slot.name: _make_slot(slot, requirement_id)
                for slot in variant.modifiers
                if slot.presence != ModifierPresence.ABSENT
            }
            for order in (tuple(active_slots), *variant.modifier_order_aliases):
                slots = tuple(
                    active_slots[name] for name in order if name in active_slots
                )
                orders.add((slots, availability))
        if orders:
            orders_by_opcode[instruction.opcode] = orders

    slots = tuple(sorted({
        slot for orders in orders_by_opcode.values() for order, _ in orders for slot in order
    }))
    slot_ids = {slot: index for index, slot in enumerate(slots)}
    return InstructionModifiers(
        slots,
        {
            opcode: tuple(sorted(
                (tuple(slot_ids[slot] for slot in order), availability)
                for order, availability in orders
            ))
            for opcode, orders in sorted(orders_by_opcode.items())
        },
        tuple(
            emit_availability(json.loads(key)) if index else "{}"
            for key, index in requirement_ids.items()
        ),
    )
