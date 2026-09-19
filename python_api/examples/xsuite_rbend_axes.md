# Xsuite RBend: three independent axes

Open **Xsuite RBend — independent axes** in the viewer's layout catalog and enable
**Mechanical axis**, **Magnetic axis** and **Beam interface**. Use **View from +Y** to
compare the bends from above. The green and gold layers show mechanical and
magnetic entry, center and exit frames; the blue layer shows the beam interface.
Hover a frame to inspect its coordinates. The Python example also prints all
three sets of entry, center and exit coordinates.

The example contains a symmetric RBend and an asymmetric RBend, both with a
4 m straight magnetic length and a 0.4 rad bend. For the asymmetric case,
`rbend_angle_diff=0.12` gives entrance/exit angles of 0.14/0.26 rad, and
`rbend_shift=-0.04` shifts the magnetic body relative to the reference trajectory.
Both explicitly enable `rbend_compensate_sagitta` and use `rbend_model="straight-body"`.
See the [Xsuite RBend parameters](https://xsuite.readthedocs.io/en/latest/apireference.html#rbend)
and [physics manual, section 1.10.5](https://xsuite.github.io/xsuite/docs/physics_manual/physics_man.pdf).

| Feature | Layout Studio representation |
| --- | --- |
| Beam interface | Curved reference trajectory, arc length `angle/h`; entry/exit normals follow its tangents. Its center is halfway along the arc. |
| Magnetic axis | Straight field axis of length `length_straight`, with parallel entrance and exit planes. |
| Mechanical axis | Centerline of the independently supplied 4.4 m engineering envelope. In the asymmetric case its center is offset by x=0.18 m and z=0.08 m, with a 0.035 rad yaw relative to the magnetic axis. |

The mechanical dimensions and placement are illustrative engineering inputs,
not additional Xtrack RBend parameters. This example maps the nominal reference
geometry; it does not track an off-momentum particle or compute a closed orbit.

In the magnetic frame, let `a_in=(angle-rbend_angle_diff)/2` and
`a_out=(angle+rbend_angle_diff)/2`. Then
`h=(sin(a_in)+sin(a_out))/length_straight`. The beam coordinate at the body's
middle plane is `x_mid=-rbend_shift+(1-cos(angle/2))/(2*h)` when compensation is
enabled. For asymmetric faces this plane does **not** contain the arc-length
midpoint, so the example calculates the beam center separately.

From the repository root, with Layout Studio installed:

```bash
python python_api/examples/xsuite_rbend_axes.py --show
python python_api/examples/xsuite_rbend_axes.py --check-xsuite
python python_api/examples/xsuite_rbend_axes.py --output webapp/public/layouts/xsuite-rbend-axes.json
```

Only `--check-xsuite` requires Xtrack, with the new straight-body RBend API.
The normal example, JSON export and frame readouts require only Layout Studio.
The Python viewer enables all three axes automatically. Each axis can also be
queried directly, for example `layout.objects["asymmetric"].get_frame("beam_center")`.
