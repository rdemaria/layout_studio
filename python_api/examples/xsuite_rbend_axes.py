"""Compare beam, magnetic and mechanical frames of straight-body Xsuite RBends.

Run with --show for the viewer, --output FILE to export, or --check-xsuite
to compare against installed Xtrack RBend objects. See xsuite_rbend_axes.md.
"""

import argparse
from math import cos, sin, sqrt

from layout_studio import Box, Frame, Layout, Position, Segment


CASES = {
    "symmetric": dict(length_straight=4.0, angle=0.4, rbend_angle_diff=0.0,
                      rbend_shift=0.0, rbend_compensate_sagitta=True),
    "asymmetric": dict(length_straight=4.0, angle=0.4, rbend_angle_diff=0.12,
                       rbend_shift=-0.04, rbend_compensate_sagitta=True),
}


def rbend_geometry(parameters):
    """Geometry for these nonzero-angle examples; all distances in m, angles in rad.

    Xsuite physics manual, section 'Rectangular bend - straight body':
    https://xsuite.github.io/xsuite/docs/physics_manual/physics_man.pdf
    Shift convention: https://xsuite.readthedocs.io/en/latest/apireference.html#rbend
    """
    straight = parameters["length_straight"]
    angle = parameters["angle"]
    difference = parameters["rbend_angle_diff"]
    angle_in, angle_out = (angle - difference) / 2, (angle + difference) / 2
    h = (sin(angle_in) + sin(angle_out)) / straight
    arc_length = angle / h
    # x_mid is the trajectory at the straight body's z=0 plane, not its arc midpoint.
    x_mid = -parameters["rbend_shift"]
    if parameters["rbend_compensate_sagitta"]:
        x_mid += 0.5 * (1 - cos(angle / 2)) / h
    px_mid = sin(angle_in) - h * straight / 2
    cos_mid = sqrt(1 - px_mid**2)
    x_in = x_mid - (cos_mid - cos(angle_in)) / h
    x_out = x_mid + (cos(angle_out) - cos_mid) / h
    center_angle = -difference / 2
    x_center = x_in + (cos(center_angle) - cos(angle_in)) / h
    z_center = -straight / 2 + (sin(angle_in) - sin(center_angle)) / h
    return dict(h=h, arc_length=arc_length, angle_in=angle_in, angle_out=angle_out,
                x_mid=x_mid, x_in=x_in, x_out=x_out, x_center=x_center,
                z_center=z_center, center_angle=center_angle)


def build_layout():
    layout = Layout()
    for index, (name, parameters) in enumerate(CASES.items()):
        geometry = rbend_geometry(parameters)
        straight = parameters["length_straight"]
        world_x = -1.2 if index == 0 else 1.2
        # Mechanical envelope data comes from engineering, independently of Xtrack.
        # The second body is longer, shifted and slightly yawed relative to the field.
        mechanical = Frame() if index == 0 else Frame().tx(0.18).tt(0.08).ry(0.035)
        kind = layout.new_type(
            name,
            color="#8798b3",
            shape=Box(0.85, 0.55, 4.4),
            mechanical_center=mechanical,
            magnetic_center=Frame(),
            magnetic_length=straight,
            magnetic_curvature=0.0,
            magnetic_roll=0.0,
        )
        layout.new_object(
            name,
            type=kind,
            position=Position("world").tx(world_x),
            beam_center=Frame().tx(geometry["x_center"]).tt(geometry["z_center"])
                               .ry(geometry["center_angle"]),
            beam_length=geometry["arc_length"],
            beam_curvature=geometry["h"],
            beam_roll=0.0,
        )
        layout.new_curve(
            f"{name}_reference",
            color="#67b7ff",
            starting_frame=Frame("world").tx(world_x + geometry["x_in"])
                .tt(-straight / 2).ry(geometry["angle_in"]).tt(-0.6),
            segments=[Segment(0.6), Segment(geometry["arc_length"], parameters["angle"]), Segment(0.6)],
        )
    return layout


def check_xsuite():
    """Optional check using the new Xtrack API (no tracker or particles needed)."""
    import xtrack as xt
    from math import isclose

    for name, parameters in CASES.items():
        bend = xt.RBend(rbend_model="straight-body", **parameters)
        geometry = rbend_geometry(parameters)
        # These internal geometric readouts are used only to verify the example.
        for actual, expected in [(bend.length, geometry["arc_length"]),
                                 (bend.h, geometry["h"]),
                                 (bend._x0_in, geometry["x_in"]),
                                 (bend._x0_mid, geometry["x_mid"]),
                                 (bend._x0_out, geometry["x_out"])]:
            assert isclose(actual, expected, abs_tol=1e-12), (name, actual, expected)
        print(f"{name}: Xtrack RBend geometry agrees")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Write a Layout Studio JSON document")
    parser.add_argument("--show", action="store_true", help="Open the interactive viewer")
    parser.add_argument("--check-xsuite", action="store_true", help="Verify with installed Xtrack")
    args = parser.parse_args()
    layout = build_layout()
    if args.check_xsuite:
        check_xsuite()
    if args.output:
        layout.to_json(args.output)
    for name, obj in layout.objects.items():
        print(f"\n{name}: world coordinates [x, y, z] in metres")
        for feature in ("beam", "magnetic", "mechanical"):
            for station in ("entry", "center", "exit"):
                frame = f"{feature}_{station}"
                print(f"  {frame:20s} {obj.get_frame(frame).origin.round(6)}")
    if args.show:
        with layout.plot_web(mechanical_axis=True, magnetic_axis=True, beam_axis=True) as viewer:
            viewer.set_view("+y")
            viewer.show()
            input("Press Enter to close the viewer… ")
