"""Check the example's interface planes and the generated viewer document."""

import importlib.util
import json
from math import cos, sin
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "xsuite_rbend_axes", ROOT / "python_api/examples/xsuite_rbend_axes.py"
)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def test_rbend_interfaces_and_arc_midpoint():
    layout = example.build_layout()
    for name, parameters in example.CASES.items():
        obj = layout.objects[name]
        curve = layout.curves[f"{name}_reference"]
        geometry = example.rbend_geometry(parameters)
        entry, center, exit = (obj.get_frame(f"beam_{where}") for where in ("entry", "center", "exit"))
        for frame, station in [(entry, 0.6), (center, 0.6 + geometry["arc_length"] / 2),
                               (exit, 0.6 + geometry["arc_length"])]:
            np.testing.assert_allclose(frame.matrix, curve.get_frame(station).matrix, atol=1e-12)
        # Both beam endpoints meet the straight field boundary planes.
        for frame, where in [(entry, "entry"), (exit, "exit")]:
            plane = obj.get_frame(f"magnetic_{where}")
            assert abs(np.dot(frame.origin - plane.origin, plane.tangent)) < 1e-12
        np.testing.assert_allclose(entry.tangent, [sin(geometry["angle_in"]), 0, cos(geometry["angle_in"])], atol=1e-12)
        np.testing.assert_allclose(exit.tangent, [-sin(geometry["angle_out"]), 0, cos(geometry["angle_out"])], atol=1e-12)

    asymmetric = layout.objects["asymmetric"]
    # Independently evaluated Xsuite straight-body geometry, including shift sign
    # and half-sagitta compensation. Arc midpoint is not in the z=0 body plane.
    np.testing.assert_allclose(asymmetric.get_frame("beam_center").origin,
                               [1.3397986417204732, 0, 0.012054629678792672], atol=1e-12, rtol=0)
    np.testing.assert_allclose(asymmetric.get_frame("beam_entry").origin,
                               [1.259273505212166, 0, -2], atol=1e-12, rtol=0)
    np.testing.assert_allclose(asymmetric.get_frame("beam_exit").origin,
                               [1.0189850898869774, 0, 2], atol=1e-12, rtol=0)
    np.testing.assert_allclose(asymmetric.get_frame("magnetic_center").origin, [1.2, 0, 0])
    np.testing.assert_allclose(asymmetric.get_frame("mechanical_center").origin, [1.38, 0, 0.08])
    assert not np.allclose(asymmetric.get_frame("mechanical_exit").tangent,
                           asymmetric.get_frame("magnetic_exit").tangent)


def test_rbend_catalog_document_matches_python_example():
    path = ROOT / "webapp/public/layouts/xsuite-rbend-axes.json"
    assert json.loads(path.read_text()) == example.build_layout().to_dict()
