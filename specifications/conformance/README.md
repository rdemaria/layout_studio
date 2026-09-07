# Positioning conformance, revision 1

Read `../layout_positioning_model.tex` for the normative equations, dependency
rules, uniqueness argument, and binary64 policy. `../layout.schema.json` is the
JSON Schema 2020-12 structural grammar. Reference existence, target rooting,
frame cycles, and geometric solvability require the additional semantic checks.

`cases.json` is a shared, frozen corpus. Expected matrices were calculated from
the specification's rigid-transform and analytic arc equations independently of
the two production solvers. The tests read the frozen numbers; they do not use
one maintained implementation as the other's oracle.

| Field | Meaning |
|---|---|
| `layout` | Input layout as a JSON value. |
| `json_text` | Raw JSON instead, used to test duplicate-member rejection. |
| `schema_valid` | Whether `layout` passes structural JSON Schema validation. |
| `frames` | Queries `{object, frame, matrix}` with expected world poses. |
| `curve_frames` | Queries `{curve, s, matrix}` with expected world poses. |
| `stations` | Queries `{curve, point, expected}`; expected is `{kind: "unique", s}` or `{kind: "ambiguous"}`. |
| `error` | Required failure `{stage, category}`. Parsing includes structural and reference validation; resolution includes all object frames and curve boundaries. |

Matrices are four row arrays with columns `[x, y, tangent, origin]` and last row
`[0,0,0,1]`. Compare each entry using `matrix_atol + matrix_rtol * abs(expected)`.
The unique-station comparisons in this corpus use an absolute tolerance of
`1e-10 m`; the specification's station-classification tolerances remain separate.
Exception wording and language-specific exception class names are not normative.
Categories are `invalid`, `no_station`, `ambiguous`, and `numeric_range`.

Run from the repository root, with the Python package available on `PYTHONPATH`:

```sh
PYTHONPATH=python_api/src python -m pytest python_api/tests/test_conformance.py
```

Run from `webapp/` after installing its dependencies:

```sh
node --test tests/conformance.test.mjs
```

Both runners resolve all existing object frames, check the expected outputs and
failures, and repeat the chained-feature example with reordered dictionaries.
The JavaScript runner also validates the corpus against the structural schema.

The cases cover anchor-relative targets, external features, false object-level
cycles, true frame cycles, instance-dependent beam frames, inheritance, contextual
`ts`, small bends, rolled and negative arcs, tangent extrapolation, station ties,
continuous solutions away from the bend plane, closed seams, tiny closed curves,
numeric overflow, and malformed JSON. A closed curve is finite and nonperiodic:
its two endpoint station numbers remain distinct even when their frames coincide.
