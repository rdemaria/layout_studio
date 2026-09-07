"""Minimal executable Layout Studio example."""

from layout_studio import Box, Frame, Layout, Position, Segment

layout = Layout()
curve = layout.new_curve(
    "main",
    starting_frame=Frame("world"),
    color="#7d91ff",
    segments=[Segment(10.0), Segment(5.0, 0.25, 0.0)],
)
kind = layout.new_type(
    "magnet",
    shape=Box(1.0, 0.8, 1.5, curvature=0.05),
    color="#f0a84b",
    magnetic_center=Frame(),
    magnetic_length=1.2,
    magnetic_curvature=0.05,
    magnetic_roll=0.0,
)
magnet = layout.new_object(
    "Q1",
    type=kind,
    position=Position(curve).ts(4.0),
)

print(magnet.get_frame("magnetic_exit"))
