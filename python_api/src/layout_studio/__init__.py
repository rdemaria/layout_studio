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
ViewerMode: TypeAlias = Literal["orbit", "pan", "select", "zoom-region"]
ViewerDirection: TypeAlias = Literal["+x", "-x", "+y", "-y", "+z", "-z"]

if TYPE_CHECKING:
    from .resolver import Resolver
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
