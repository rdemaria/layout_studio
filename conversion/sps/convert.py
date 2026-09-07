#!/usr/bin/env python3
"""SPS snapshot policy: hierarchy containers carry s-spans, hardware is straight."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ldb_machine_to_layout import ConversionError, machine_to_layout, main as run
else:
    from ..ldb_machine_to_layout import ConversionError, machine_to_layout, main as run


SPAN_TYPES = frozenset({"SPS SEXTANT", "SPS HALF ARC", "SPS LSS", "SPS PERIOD"})


def convert(machine, *, span_types=(), **options):
    if machine.name != "SPS":
        raise ConversionError("the SPS policy requires an SPS snapshot")
    return machine_to_layout(machine, span_types=SPAN_TYPES | set(span_types), **options)


if __name__ == "__main__":
    raise SystemExit(run(convert=convert, default_input=Path(__file__).with_name("SPS--LS3.pickle")))
