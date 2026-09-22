#!/usr/bin/env python3
"""BR policy: PSB periods are spans; coincident ring bends share one path."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ldb_machine_to_layout import ConversionError, main as run
    from _ring_conversion import convert_ring
else:
    from ..ldb_machine_to_layout import ConversionError, main as run
    from .._ring_conversion import convert_ring


SPAN_TYPES = frozenset({"PSB PERIOD"})


def convert(machine, *, root_name=None, span_types=(), **options):
    if machine.name != "BR":
        raise ConversionError("the BR policy requires a BR snapshot")
    return convert_ring(machine, root_name=root_name or "PSB",
                        span_types=SPAN_TYPES | set(span_types),
                        merge_coincident_bends=True, **options)


if __name__ == "__main__":
    raise SystemExit(run(convert=convert, default_input=Path(__file__).with_name("BR--LS3.pickle")))
