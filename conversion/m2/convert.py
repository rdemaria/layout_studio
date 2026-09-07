#!/usr/bin/env python3
"""M2 snapshot policy: M2-LINE is the root; physical mechanical lengths are straight."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ldb_machine_to_layout import ConversionError, machine_to_layout, main as run
else:
    from ..ldb_machine_to_layout import ConversionError, machine_to_layout, main as run


def convert(machine, *, root_name=None, **options):
    if machine.name != "M2":
        raise ConversionError("the M2 policy requires an M2 snapshot")
    return machine_to_layout(machine, root_name=root_name or "M2-LINE", **options)


if __name__ == "__main__":
    raise SystemExit(run(convert=convert, default_input=Path(__file__).with_name("M2--LS3.pickle")))
