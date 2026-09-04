"""Curve-referenced layout modelling for interactive Python workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypeAlias

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

Projection2D: TypeAlias = Literal["xy", "yx", "xz", "zx", "yz", "zy"]
ViewerMode: TypeAlias = Literal["orbit", "pan", "select", "zoom-region"]
ViewerDirection: TypeAlias = Literal["+x", "-x", "+y", "-y", "+z", "-z"]

if TYPE_CHECKING:
    from .resolver import Resolver
    from .viewer2d import LayoutViewer2D
    from .webviewer import (
        WebViewer,
        WebViewerAssetError,
        WebViewerError,
        WebViewerTimeoutError,
    )


def __getattr__(name: str) -> Any:
    if name == "Resolver":
        from .resolver import Resolver

        return Resolver
    if name == "LayoutViewer2D":
        from .viewer2d import LayoutViewer2D

        return LayoutViewer2D
    if name in {
        "WebViewer",
        "WebViewerAssetError",
        "WebViewerError",
        "WebViewerTimeoutError",
    }:
        from .webviewer import (
            WebViewer,
            WebViewerAssetError,
            WebViewerError,
            WebViewerTimeoutError,
        )

        return {
            "WebViewer": WebViewer,
            "WebViewerAssetError": WebViewerAssetError,
            "WebViewerError": WebViewerError,
            "WebViewerTimeoutError": WebViewerTimeoutError,
        }[name]
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
    "Projection2D",
    "Reference",
    "ReferenceCycleError",
    "ReferenceInUseError",
    "ReferenceLike",
    "Resolver",
    "RootEntity",
    "RootKind",
    "SearchEntity",
    "SearchKind",
    "Segment",
    "StationOutOfRangeError",
    "Type",
    "UnknownEntityError",
    "ValidationError",
    "ViewerDirection",
    "ViewerMode",
    "WebViewer",
    "WebViewerAssetError",
    "WebViewerError",
    "WebViewerTimeoutError",
    "WorldReference",
]
