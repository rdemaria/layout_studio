from __future__ import annotations

import copy
import math

import numpy as np
import pytest


@pytest.fixture
def canonical_layout_dict() -> dict[str, object]:
    """A small but fully representative canonical layout document."""

    return {
        "reference_curves": {
            "main": {
                "color": "#7d91ff",
                "starting_frame": {
                    "reference": {"kind": "world"},
                    "transformation": [["tx", 1.25]],
                },
                "segments": [
                    [4.0, 0.0, 0.0],
                    [math.pi / 2.0, math.pi / 2.0, 0.0],
                ],
            }
        },
        "types": {
            "magnet": {
                "shape": ["box", 2.0, 1.0, 2.0, 0.0, 0.0],
                "color": "#f0a84b",
                "magnetic_center": {
                    "transformation": [["tx", 0.1]],
                },
                "magnetic_length": 1.5,
                "frames": {
                    "survey": {
                        "transformation": [["ts", 0.25], ["rx", 0.1]],
                    }
                },
            }
        },
        "objects": {
            "Q1": {
                "type": "magnet",
                "position": {
                    "target": "center",
                    "reference": {"kind": "curve", "curve": "main"},
                    "transformation": [["ts", 2.0]],
                },
            }
        },
    }


@pytest.fixture
def canonical_copy(canonical_layout_dict):
    return lambda: copy.deepcopy(canonical_layout_dict)


def assert_pose(pose, *, origin=None, x=None, y=None, tangent=None, atol=1e-12):
    if origin is not None:
        np.testing.assert_allclose(pose.origin, origin, atol=atol, rtol=0.0)
    if x is not None:
        np.testing.assert_allclose(pose.x, x, atol=atol, rtol=0.0)
    if y is not None:
        np.testing.assert_allclose(pose.y, y, atol=atol, rtol=0.0)
    if tangent is not None:
        np.testing.assert_allclose(pose.tangent, tangent, atol=atol, rtol=0.0)
