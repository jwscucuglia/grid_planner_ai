"""Exact private material balances; exported contracts keep finite floats.

Input quantities denote their decimal ``str`` values. Fraction arithmetic has
no precision context, so even a subnormal parcel added to a large balance is
retained. Display floats must never be used to debit physical inventory.
"""
from fractions import Fraction
import sys
from typing import Dict, Iterable, Mapping, Union

from .contracts import ServiceOrder


MaterialQuantity = Union[int, float, Fraction]
_MAX_MATERIAL_FLOAT = Fraction(str(sys.float_info.max))


def exact_materials(quantities: Mapping[str, MaterialQuantity]) -> Dict[str, Fraction]:
    return {material: q if isinstance(q, Fraction) else Fraction(str(q))
            for material, q in quantities.items()}


def service_material_totals(services: Iterable[ServiceOrder]) -> Dict[str, Fraction]:
    totals: Dict[str, Fraction] = {}
    for service in services:
        for material, quantity in service.required_materials.items():
            totals[material] = totals.get(material, Fraction(0)) + Fraction(str(quantity))
    return totals


def subtract_service_materials(
    stock: Mapping[str, MaterialQuantity], services: Iterable[ServiceOrder],
) -> Dict[str, Fraction]:
    remaining = exact_materials(stock)
    for material, quantity in service_material_totals(services).items():
        remaining[material] = remaining.get(material, Fraction(0)) - quantity
    return remaining


def export_material_totals(totals: Mapping[str, Fraction]) -> Dict[str, float]:
    for material, quantity in totals.items():
        if quantity > _MAX_MATERIAL_FLOAT:
            raise ValueError(f"Material '{material}': total não representável como float finito.")
    return {material: float(quantity) for material, quantity in totals.items()}
