# M2 LS3 conversion

This directory records the M2 LS3 regression conversion from the CERN Layout
Database positioning model to Layout Studio.

## Reproduce

Place the trusted `M2--LS3.pickle` snapshot in this directory, then run from the
repository root:

```bash
python conversion/ldb_machine_to_layout.py \
  conversion/m2/M2--LS3.pickle \
  --output conversion/m2/M2--LS3.layout.json \
  --report conversion/m2/M2--LS3.layout-report.json \
  --indent 0
```

No M2-specific options are required. The converter detects `M2-LINE` as the
external root because it is a machine-name alias, and derives `1185.5781 m` as
the last direct mechanical/optic endpoint. It then calls
`Machine.get_ref_curve(machine_length=1185.5781, ...)`; the stale cached LHC
length `26658.8832 m` is ignored.

The explicit equivalent is:

```bash
python conversion/ldb_machine_to_layout.py conversion/m2/M2--LS3.pickle \
  --root-name M2-LINE \
  --machine-length 1185.5781
```

## Result

- Input transformations: 428
- Output objects: 428
- Output types: 115
- Reference-curve segments: 29
- Reference-curve length: 1185.5781 m
- Reference-curve total bend angle: 0.0222839 rad
- Skipped objects: 0

## Validation

- Python syntax compilation.
- Strict canonical Layout Studio JSON structural validation.
- Three regression tests for root aliases, stale cached curve lengths and true
  dangling branches.
- Reference-curve comparison at all 29 segment boundaries against
  `LDBPoint.to_madpoint()`: maximum position difference below `3e-13 m` and
  maximum orientation-matrix coefficient difference below `6e-16`.
