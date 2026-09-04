# CERN Layout Database conversion

This directory contains a standalone converter from a CERN Layout Database
`Machine` snapshot to canonical Layout Studio JSON.

## Contents

- `ldb_machine_to_layout.py` — reusable `machine_to_layout()` function and CLI.
- `machine.py` — LDB machine, transformation, type, aperture and profile classes.
- `ldbpoint.py` — LDB, MAD-X and CCS frame conversion utilities.
- `requirements.txt` — standalone runtime dependencies.
- `sps/` — SPS LS3 conversion documentation, report and artifact manifest.

The converter first attempts to import the installed `cernlayoutdb` package. If
it is unavailable, it loads the standalone `machine.py` and `ldbpoint.py` files
from this directory.

## Run

From the repository root, with `SPS--LS3.pickle` available locally:

```bash
python -m pip install -r conversion/requirements.txt
python conversion/ldb_machine_to_layout.py \
  SPS--LS3.pickle \
  --output SPS--LS3.layout.json \
  --report SPS--LS3.layout-report.json
```

Use `--indent 0` for compact JSON. Pickle files must be treated as trusted
project data even though the converter uses a restricted class allow-list.

See [`sps/README.md`](sps/README.md) for SPS-specific conversion decisions and
validation results.
