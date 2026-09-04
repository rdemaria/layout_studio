import pickle
from collections import defaultdict
import re

from ldbpoint import LDBPoint, LDBPath


class LDBTrans:
    c_transformations = (
        "FROM_LAYOUT_NAME",
        "FROM_POINT_TYPE_NAME",
        "TO_LAYOUT_NAME",
        "TO_LAYOUT_TYPE_NAME",
        "TO_POINT_TYPE_NAME",
        "TX",
        "TY",
        "TZ",
        "RX",
        "RY",
        "RZ",
        "TX_ORDER",
        "TY_ORDER",
        "TZ_ORDER",
        "RX_ORDER",
        "RY_ORDER",
        "RZ_ORDER",
        "MECHANICAL_LENGTH",
        "OPTIC_LENGTH",
        "MECHANICAL_OPTIC_MIDDLE_OFFSET",
        "DEFLECTION_ANGLE",
        "ROLL_ANGLE",
        "SEQUENCES",
#        "IGNORE_YZ",
        "MECMAG_MIDDLE_OFFSET_LONGITUDINAL",
        "MECMAG_MIDDLE_OFFSET_RADIAL",
        "MECMAG_MIDDLE_OFFSET_VERTICAL",
    )
    abbrev = {
        "MECHANICAL START": "MS",
        "MECHANICAL MIDDLE": "MM",
        "MECHANICAL END": "ME",
        "OPTIC START": "OS",
        "OPTIC MIDDLE": "OM",
        "OPTIC END": "OE",
    }
    default_points = list(abbrev)

    c_aperture_profiles = None

    @classmethod
    def machine_from_ldb(cls, machine="LHC", version="EYETS 2024-2025"):
        tbs = Table.from_ldb(
            tablename="LAYOUT_TRANSFORMATIONS_V",
            cols=cls.c_transformations,
            index="TO_LAYOUT_NAME",
            where=f"MACHINE='{machine}' AND VERSION='{version}'",
        )
        transformations = {}
        for row in tbs.rows:
            t = LDBTrans.from_row(row)
            transformations[t.target] = t
        return transformations

    @classmethod
    def from_row(cls, row):
        order = (
            (row.TX_ORDER, "tx"),
            (row.TY_ORDER, "ty"),
            (row.TZ_ORDER, "tz"),
            (row.RX_ORDER, "rx"),
            (row.RY_ORDER, "ry"),
            (row.RZ_ORDER, "rz"),
        )
        order = [t for _, t in sorted(order, reverse=True)]
        return cls(
            row.TO_LAYOUT_NAME,
            row.TO_LAYOUT_TYPE_NAME,
            row.TO_POINT_TYPE_NAME,
            row.FROM_LAYOUT_NAME,
            row.FROM_POINT_TYPE_NAME,
            row.TX,
            row.TY,
            row.TZ,
            row.RX,
            row.RY,
            row.RZ,
            order,
            row.MECHANICAL_LENGTH,
            row.OPTIC_LENGTH,
            row.MECHANICAL_OPTIC_MIDDLE_OFFSET,
            row.DEFLECTION_ANGLE,
            row.ROLL_ANGLE,
            row.SEQUENCES,
#            row.IGNORE_YZ,
            row.MECMAG_MIDDLE_OFFSET_LONGITUDINAL,
            row.MECMAG_MIDDLE_OFFSET_RADIAL,
            row.MECMAG_MIDDLE_OFFSET_VERTICAL,
        )

    def __init__(
        self,
        target,
        target_type,
        target_point,
        ref,
        ref_point,
        tx,
        ty,
        tz,
        rx,
        ry,
        rz,
        order,
        length,
        optic_length,
        optic_offset,
        angle,
        roll,
        sequences,
#        ignore_yz,
        mec_long_offset,
        mec_radial_offset,
        mec_vertical_offset,
    ):
        self.target = target
        self.target_type = target_type
        self.target_point = target_point
        self.ref = ref
        self.ref_point = ref_point
        self.tx = tx
        self.ty = ty
        self.tz = tz
        self.rx = rx
        self.ry = ry
        self.rz = rz
        self.order = order
        self.length = length
        self.optic_length = optic_length
        self.optic_offset = optic_offset
        self.angle = angle
        self.roll = roll
        self.sequences = sequences
#        self.ignore_yz = ignore_yz
        self.mec_long_offset = mec_long_offset
        self.mec_radial_offset = mec_radial_offset
        self.mec_vertical_offset = mec_vertical_offset

    def __repr__(self):
        trans = []
        for t in self.order:
            v = getattr(self, t)
            if v != 0:
                trans.append(f"{t}({getattr(self, t)})")
        trans = ".".join(trans)
        tp = self.abbrev.get(self.target_point, self.target_point)
        rp = self.abbrev.get(self.ref_point, self.ref_point)
        return f"<T {self.target}/{tp}: {self.ref}/{rp} {trans}>"

    def get_points(self, start=None):
        """ "return a list of points relative the mechanical middle"""
        mm = LDBPoint()
        ms = LDBPoint(x=-self.length / 2)
        me = LDBPoint(x=self.length / 2)
        om = mm.c.tx(self.optic_offset)
        os = om.c.tx(-self.optic_length / 2)
        oe = om.c.tx(self.optic_length / 2)

        res = (ms, mm, me, os, om, oe)
        if start is not None:
            res = tuple(start @ pp for pp in res)
        return dict(zip(self.default_points, res))

    def get_transformation(self):
        res = LDBPoint()
        for t in self.order:
            tval = getattr(self, t)
            if tval != 0:
                getattr(res, t)(tval)
        return res

    def get_transformation_ref_curve(self, ref):
        res = LDBPoint()
        for t in self.order:
            tval = getattr(self, t)
            if tval != 0:
                getattr(res, t)(tval)
        return res


class Profile:
    c_aperture_profiles = (
        "APERTURE_ALIAS",
        "APERTURE_PROFILE_ID",
        "APERTURE_SHAPE",
        "INNER_DIAMETER_Y",
        "INNER_DIAMETER_Z",
        "ELLIPSE_PARAM_A",
        "ELLIPSE_PARAM_B",
        "ELLIPSE_PARAM_C",
        "ELLIPSE_PARAM_D",
        "NOTE",
        "CUSTOM_APERTURE_PROFILE",
    )

    @staticmethod
    def get_all_profiles(db=None):
        if db is None:
            db = DB()

        rows = db.get_view(
            "APERTURE_PROFILES",
            Profile.c_aperture_profiles,
        ).rows
        profiles = {}
        for row in sorted(rows):
            profile = Profile.from_row(row)
            profiles[profile.name] = profile
        return profiles

    @classmethod
    def from_row(cls, row):
        return cls(
            name=row.APERTURE_ALIAS,
            shape=row.APERTURE_SHAPE,
            inner_y=row.INNER_DIAMETER_Y,
            inner_z=row.INNER_DIAMETER_Z,
            ellipse_a=row.ELLIPSE_PARAM_A,
            ellipse_b=row.ELLIPSE_PARAM_B,
            ellipse_c=row.ELLIPSE_PARAM_C,
            ellipse_d=row.ELLIPSE_PARAM_D,
            note=row.NOTE,
            custom=row.CUSTOM_APERTURE_PROFILE,
        )

    @classmethod
    def from_ldb(cls, profile_alias, db=None):
        if db is None:
            db = DB()

        row = db.get_view(
            "APERTURE_PROFILES",
            cls.c_aperture_profiles,
            f"WHERE APERTURE_ALIAS='{profile_alias}'",
        )
        if row is None:
            return None
        return cls.from_row(row)

    def __init__(
        self,
        name,
        shape,
        inner_y,
        inner_z,
        ellipse_a,
        ellipse_b,
        ellipse_c,
        ellipse_d,
        note,
        custom,
    ):
        self.name = name
        self.shape = shape
        self.inner_y = inner_y
        self.inner_z = inner_z
        self.ellipse_a = ellipse_a
        self.ellipse_b = ellipse_b
        self.ellipse_c = ellipse_c
        self.ellipse_d = ellipse_d
        self.note = note
        self.custom = custom

    def __repr__(self):
        custom_str = " CUSTOM" if self.custom else ""
        return f"<Profile {self.name} {self.shape}, {self.inner_y},{self.inner_z}{custom_str}>"


class Aperture:
    c_elem_type_apertures = (
        "TYPE_NAME",
        "OFFSET_X",
        "OFFSET_Y",
        "OFFSET_Z",
        "APERTURE_ALIAS",
        "ELEMENT_TYPE_APERTURE",
        "ELEMENT_TYPE_TOLERANCE",
    )

    @classmethod
    def from_table(cls, tab):
        return cls(
            name=tab["TYPE_NAME"],
            offset_x=tab["OFFSET_X"],
            offset_y=tab["OFFSET_Y"],
            offset_z=tab["OFFSET_Z"],
            aperture_alias=tab["APERTURE_ALIAS"],
            element_type_aperture=tab["ELEMENT_TYPE_APERTURE"],
            element_type_tolerance=tab["ELEMENT_TYPE_TOLERANCE"],
        )

    @classmethod
    def from_ldb(cls, type_name, db=None):
        if db is None:
            db = DB()

        row = Table.from_ldb(
            tablename="ELEMENT_TYPE_APERTURES_V",
            cols=cls.c_elem_type_apertures,
            where=f"TYPE_NAME='{type_name}'",
        )
        return cls.from_table(row)

    def __init__(
        self,
        name,
        offset_x,
        offset_y,
        offset_z,
        aperture_alias,
        element_type_aperture,
        element_type_tolerance,
    ):
        self.name = name
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.offset_z = offset_z
        self.aperture_alias = aperture_alias
        self.element_type_aperture = element_type_aperture
        self.element_type_tolerance = element_type_tolerance

    def __repr__(self):
        return f"<Aperture {len(self.aperture_alias)} profiles>"

    def info(self):
        print(f"Aperture for type {self.name[0]}:")
        print("  Alias      (     x       y       z) IE  tol")
        for aa, xx, yy, zz, etapt, etolt in zip(
            self.aperture_alias,
            self.offset_x,
            self.offset_y,
            self.offset_z,
            self.element_type_aperture,
            self.element_type_tolerance,
        ):
            print(f"  {aa:6s}: at ({xx:6.3f}, {yy:6.3f}, {zz:6.3f}) {etapt:2s} {etolt}")


class Type:
    @staticmethod
    def get_all_types_with_apertures(db=None):
        if db is None:
            db = DB()

        rows = db.get_view(
            "ELEMENT_TYPE_APERTURES_V",
            Aperture.c_elem_type_apertures,
        ).rows
        types = defaultdict(list)
        for row in sorted(rows):
            types[row.TYPE_NAME].append(row)

        types_final = {}
        for type_name, rows in types.items():
            aptable = {vv: list() for vv in Aperture.c_elem_type_apertures}
            for row in rows:
                for vv in Aperture.c_elem_type_apertures:
                    aptable[vv].append(getattr(row, vv))
            types_final[type_name] = Type(
                type_name, aperture=Aperture.from_table(aptable)
            )
        return types_final

    @classmethod
    def from_ldb(cls, type_name, db=None):
        Aperture.from_ldb(type_name, db)
        return cls(type_name, Aperture.from_ldb(type_name, db))

    def __init__(self, name, aperture):
        self.name = name
        self.aperture = aperture

    def __repr__(self):
        ap = f" {self.aperture}" if self.aperture else ""
        return f"<Type {self.name}{ap}>"


class Machine:
    @classmethod
    def from_ldb(cls, machine="LHC", version="YETS 2025-2026"):
        transformations = LDBTrans.machine_from_ldb(machine, version)
        all_types = Type.get_all_types_with_apertures()
        all_profiles = Profile.get_all_profiles()
        types = {}
        for t in transformations.values():
            types[t.target_type] = all_types.get(
                t.target_type, Type(t.target_type, None)
            )
        profiles = {}
        for tt in all_types.values():
            for aa in tt.aperture.aperture_alias:
                profiles[aa] = all_profiles[aa]
        return cls(machine, version, transformations, types, profiles)

    @classmethod
    def from_pickle(cls, filename):
        return pickle.load(open(filename, "rb"))

    def __init__(
        self,
        name="LHC",
        version="EYETS 2024-2025",
        transformations=None,
        types=None,
        profiles=None,
    ):
        self.name = name
        self.version = version
        self.transformations = transformations
        self.types = types
        self.profiles = profiles
        self.ref_curve = self.get_ref_curve()

    def to_pickle(self, filename):
        pickle.dump(self, open(filename, "wb"))

    def __repr__(self):
        return f"<Machine {self.name}, {self.version}, {len(self.transformations)} trans>"

    def search(self, regexp):
        return [k for k in self.transformations if re.match(regexp, k)]

    def info(self, element):
        tf = self.transformations.get(element, None)
        if tf is None:
            raise KeyError(f"Element {element} not found in machine")
        print(self.types[tf.target_type])


    def get_hierarchy(self, target):
        hierarchy = []
        while target in self.transformations:
            tf = self.transformations[target]
            hierarchy.insert(0, tf)
            target = tf.ref
        return hierarchy

    def get_abs_points(self, element, start=None):
        """
        Compute the absolute positions of the points in the LDB framework.
        Machine is always straight.

        The transformation is always from the reference element to the target element.
        p1-----o1-----|
            |-----o2-----p2
        to1p1 transformation from o1 to reference point p1
        to2p2 transformation from o2 to target point p2
        tp1p2 transformation from p1 to p2
        to1o2 transformation from o1 to o2

        """
        if start is None:
            o1_abs = LDBPoint()  # origin of the refrence element (mm)
        hierarchy = self.get_hierarchy(element)
        points_1_rel = {
            k: LDBPoint() for k in LDBTrans.default_points
        }  # relative points of the reference elements
        for tf in hierarchy:
            points_2_rel = tf.get_points()  # relative points of the target
            to1p1 = points_1_rel[tf.ref_point]  # transf mm ref to ref point
            to2p2 = points_2_rel[
                tf.target_point
            ]  # transf mm target to target point
            tp1p2 = tf.get_transformation()  # transf ref point to target point
            to1o2 = to1p1 @ tp1p2 @ to2p2.inv()  # transf mm ref to target mm
            # print(f"{tf.target}/MM = {tf.ref}/MM + {to1o2.x}" )
            points_2_abs = tf.get_points(
                o1_abs @ to1o2
            )  # absolute points of the target
            # print(tf.target, "S", points_2_abs["MECHANICAL START"])
            # print(tf.target, "E", points_2_abs["MECHANICAL END"])
            o1_abs = points_2_abs[
                "MECHANICAL MIDDLE"
            ]  # update reference point for next transformation
            points_1_rel = (
                points_2_rel  # update relative points for next transformation
            )
        return points_2_abs

    def get_rel_points(self, element, start=None):
        if start is None:
            start = LDBPoint()
        return self.transformations[element].get_points(start)

    def get_bends(self):
        """
        Return bends on the reference trajectory
        """
        out = []
        for name, tf in self.transformations.items():
            if tf.angle != 0:
                bpoints = self.get_abs_points(name)
                os = bpoints["OPTIC START"]
                oe = bpoints["OPTIC END"]
                if os.y == 0 and oe.y == 0:
                    out.append(
                        (name, os.x, oe.x, tf.optic_length, tf.angle, tf.roll)
                    )
        out.sort(key=lambda x: x[1])
        return out

    def get_ref_curve(self, machine_length=26658.8832, start=None):
        segments = []
        last = 0
        for name, os, oe, length, angle, roll in self.get_bends():
            segments.append((os - last, 0, 0))  # Drift
            # the roll in layout database follow MAD-X convention
            # positive roll make dipole point downwards
            segments.append((length, angle, -roll))  # Bend
            last = oe
        segments.append((machine_length - last, 0, 0))
        return LDBPath(segments, start=start)

    def get_abs_curved_points(self, element, start=None, ref=None):
        """
        Return the points on the reference trajectory

        If y=0 and z=0 pick the point on the ref curve and transform
        else transform.

        """
        if start is None:
            o1_abs = LDBPoint()  # origin of the refrence element (mm)
        else:
            o1_abs = start

        if ref is None:
            ref = self.get_ref_curve()
        hierarchy = self.get_hierarchy(element)

        points_1_rel = {
            k: LDBPoint() for k in LDBTrans.default_points
        }  # relative points of the reference elements


        # find the position on the reference curve
        s_ref=0
        ii=0
        for tf in hierarchy:
            points_2_rel = tf.get_points()  # relative points of the target
            to1p1 = points_1_rel[tf.ref_point]  # transf mm ref to ref point
            to2p2 = points_2_rel[
                tf.target_point
            ]  # transf mm target to target point
            if tf.ty==0 and tf.tz==0 and tf.ry==0 and tf.rz==0 and tf.rx==0:
                # tf.tx= T_p1p2 = T_o1o2 - to1p1 + to2p2
                # T_o1o2 = T_p1p2 + to1p1 - to2p2
                s_ref+= tf.tx + to1p1.x - to2p2.x
                ii+=1
            else:
                break

        print(s_ref, ii)
        o1_abs = ref.get_point(s_ref)
        if ii==len(hierarchy):
            return tf.get_points(o1_abs)

        for tf in hierarchy[ii:]:
            points_2_rel = tf.get_points()  # relative points of the target
            to1p1 = points_1_rel[tf.ref_point]  # transf mm ref to ref point
            to2p2 = points_2_rel[
                tf.target_point
            ]  # transf mm target to target point
            tp1p2 = tf.get_transformation()  # transf ref point to target point
            to1o2 = to1p1 @ tp1p2 @ to2p2.inv()  # transf mm ref to target mm
            # print(f"{tf.target}/MM = {tf.ref}/MM + {to1o2.x}" )
            points_2_abs = tf.get_points(
                o1_abs @ to1o2
            )  # absolute points of the target
            # print(tf.target, "S", points_2_abs["MECHANICAL START"])
            # print(tf.target, "E", points_2_abs["MECHANICAL END"])
            o1_abs = points_2_abs[
                "MECHANICAL MIDDLE"
            ]  # update reference point for next transformation
            points_1_rel = (
                points_2_rel  # update relative points for next transformation
            )
        return points_2_abs
