# CERN Layout Database conversion

This directory contains a standalone converter from a CERN Layout Database
`Machine` snapshot to canonical Layout Studio JSON.

## Contents

- `ldb_machine_to_layout.py` — public `machine_to_layout()` function and CLI, including generic root and length discovery.
- `_ldb_machine_to_layout_core.py` — conversion implementation shared by the public entry point.
- `machine.py` — LDB machine, transformation, type, aperture and profile classes.
- `ldbpoint.py` — LDB, MAD-X and CCS frame conversion utilities.
- `requirements.txt` — standalone runtime dependencies.
- `tests/` — converter regression tests, including transfer-line root aliases.
- `sps/` and `m2/` — machine-specific conversion notes and reports.

The converter first attempts to import the installed `cernlayoutdb` package. If
it is unavailable, it loads the standalone `machine.py` and `ldbpoint.py` files
from this directory.

## Run

From the repository root:

```bash
python -m pip install -r conversion/requirements.txt
python conversion/ldb_machine_to_layout.py \
  M2--LS3.pickle \
  --output M2--LS3.layout.json \
  --report M2--LS3.layout-report.json
```

Use `--indent 0` for compact JSON. Pickle files must be treated as trusted
project data even though the converter uses a restricted class allow-list.

Converted LDB optic data is emitted as a complete magnetic-axis feature,
including explicit length, curvature, roll, and center transformation. The
current conversion gives the mechanical shape the same path parameters, while
the Layout Studio model itself keeps those two axes independent.

## Root and machine-length resolution

The LDB machine name is not always the name of the external frame used by the
transformations. Rings normally reference `LHC` or `SPS`, while the M2 snapshot
references `M2-LINE`. The converter resolves the curve root in this order:

1. explicit `--root-name`;
2. exact match with `machine.name`;
3. a machine-name alias such as `M2-LINE`;
4. the only or clearly dominant external reference.

Other unresolved references remain dangling objects and follow `--dangling`.
The selected root is recorded in the conversion report.

The cached `machine.ref_curve` length is never used as a fallback, because the
supplied `Machine` class constructs it with the LHC default circumference even
for transfer lines and other rings. Instead, the converter derives the maximum
mechanical or optic endpoint among objects positioned directly on the selected
root and calls:

```python
machine.get_ref_curve(machine_length=inferred_length, start=start)
```

`--machine-length` still overrides the inferred value. For unusual snapshots
where root or length detection is ambiguous, specify both explicitly:

```bash
python conversion/ldb_machine_to_layout.py machine.pickle \
  --root-name MY-LINE \
  --machine-length 1234.5
```

See [`m2/README.md`](m2/README.md) and [`sps/README.md`](sps/README.md) for the
validated example conversions.
