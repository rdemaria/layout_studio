"""Curve-referenced layout modelling for interactive Python workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import (
    AmbiguousNameError,
    AmbiguousStationError,
    AttachmentError,
    DanglingReferenceError,
    EvaluationError,
    ForeignLayoutError,
    LayoutError,
    NameConflictError,
    NoStationSolutionError,
    ReferenceCycleError,
    ReferenceInUseError,
    StationOutOfRangeError,
    UnknownEntityError,
    ValidationError,
)
from .model import (
    UNSET,
    Box,
    Curve,
    CurveReference,
    Cylinder,
    EntityMap,
    Frame,
    FrameLike,
    JsonValue,
    Layout,
    ManagedSequence,
    Object,
    ObjectReference,
    Operation,
    OperationName,
    OwnedValue,
    Pose,
    Position,
    Reference,
    ReferenceLike,
    RootEntity,
    RootKind,
    SearchEntity,
    SearchKind,
    Segment,
    Type,
    WorldReference,
)
from .viewer import LayoutViewer

if TYPE_CHECKING:
    from .viewer2d import LayoutViewer2D


def __getattr__(name: str) -> Any:
    if name == "LayoutViewer2D":
        from .viewer2d import LayoutViewer2D

        return LayoutViewer2D
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "UNSET",
    "AmbiguousNameError",
    "AmbiguousStationError",
    "AttachmentError",
    "Box",
    "Curve",
    "CurveReference",
    "Cylinder",
    "DanglingReferenceError",
    "EntityMap",
    "EvaluationError",
    "ForeignLayoutError",
    "Frame",
    "FrameLike",
    "JsonValue",
    "Layout",
    "LayoutError",
    "LayoutViewer",
    "LayoutViewer2D",
    "ManagedSequence",
    "NameConflictError",
    "NoStationSolutionError",
    "Object",
    "ObjectReference",
    "Operation",
    "OperationName",
    "OwnedValue",
    "Pose",
    "Position",
    "Reference",
    "ReferenceCycleError",
    "ReferenceInUseError",
    "ReferenceLike",
    "RootEntity",
    "RootKind",
    "SearchEntity",
    "SearchKind",
    "Segment",
    "StationOutOfRangeError",
    "Type",
    "UnknownEntityError",
    "ValidationError",
    "WorldReference",
]
