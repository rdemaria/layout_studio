# Layout Studio Python API

`layout-studio` is the Python counterpart of Layout Studio: it describes
piecewise circular reference curves, reusable curved component types, and
objects positioned by aligning named frames.

Distances are in metres, rotations are in radians, and curvature is in
metres⁻¹.  The homogeneous pose convention is `[x y tangent origin]`.

## Install for development

```bash
python -m pip install -e '.[viewer,test]'
```

VTK is optional until a 3D view is requested.  The geometric model and JSON
round trips need only NumPy.

## Quick start

```python
from layout_studio import Box, Frame, Layout, Position, Segment

layout = Layout()

main = layout.new_curve(
    "main",
    starting_frame=Frame("world").tx(1.0),
    color="#7aa2f7",
    segments=[Segment(10.0), Segment(5.0, angle=0.2, roll=0.01)],
)

quadrupole = layout.new_type(
    "quadrupole",
    shape=Box(1.1, 0.9, 1.6, curvature=0.22),
    color="#f0a84b",
    magnetic_center=Frame(),
    magnetic_length=1.4,
)
quadrupole.new_frame("survey_mark").tx(0.4)

q1 = layout.new_object(
    "Q1",
    type=quadrupole,
    position=Position("curve:main", target="center").ts(3.1).tt(0.2),
)

q2 = layout.new_object(
    "Q2",
    type=quadrupole,
    position=Position(
        "Q1->magnetic_exit",
        target="magnetic_entry",
        reference_curve=main,
    ).ts(0.3).tx(0.001),
)

layout.validate()
print(q2.get_frame("magnetic_exit"))
layout.save("layout.json")

# Both spellings are available because they were used in the original API
# discussion.  They return a LayoutViewer; show=False builds without starting
# the blocking native interactor.
viewer = layout.plot3D(beam_frames=True, show=False)
viewer.show()

# Entity methods deliberately keep the visible scene local to that entity.
main.plot3d()
q1.plot3D(beam_frames=True)
```

## Reference shorthand

Generic references accept only unambiguous strings:

| Input | Reference |
| --- | --- |
| `"world"` | World frame |
| `"curve:main"` | Curve `main` |
| `"Q1->magnetic_exit"` | `Q1` Beam-exit frame |

Bare names are still accepted in namespace-specific fields such as
`type="quadrupole"`, `target="magnetic_entry"`, and
`reference_curve="main"`.  JSON always uses the canonical structured form.

## Viewer controls

The VTK viewer uses trackball-camera navigation.  Left drag orbits, middle
drag pans, and the wheel zooms.  Click an object or curve to highlight it and
show its world pose; hover shows its name.  Keyboard shortcuts toggle the
principal layers and restore the fitted camera:

| Key | Action |
| --- | --- |
| `c` | Reference curves |
| `o` | Objects |
| `b` | Beam entry/exit frames |
| `f` or `r` | Fit/reset camera |
| `Escape` | Clear selection |

The `LayoutViewer` also exposes `set_curves_visible()`,
`set_objects_visible()`, `set_beam_frames_visible()`, `fit()`, `select()`,
`screenshot()`, and `close()` for interactive Python use.
