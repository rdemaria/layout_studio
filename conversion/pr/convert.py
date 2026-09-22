#!/usr/bin/env python3
"""PR policy: PS lattice containers are spans; adjacent bends have no drift."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ldb_machine_to_layout import ConversionError, main as run
    from _ring_conversion import convert_ring
else:
    from ..ldb_machine_to_layout import ConversionError, main as run
    from .._ring_conversion import convert_ring


SPAN_TYPES = frozenset({
    "PS RING SECTOR", "PS RING SECTION", "PS RING STRAIGHT SECTION",
    "PS RING UNIT ASSEMBLY SECTION",
})


def convert(machine, *, root_name=None, span_types=(), **options):
    if machine.name != "PR":
        raise ConversionError("the PR policy requires a PR snapshot")
    return convert_ring(machine, root_name=root_name or "PS",
                        span_types=SPAN_TYPES | set(span_types), **options)


if __name__ == "__main__":
    raise SystemExit(run(convert=convert, default_input=Path(__file__).with_name("PR--LS3.pickle")))
