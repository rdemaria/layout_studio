"""Exception hierarchy for Layout Studio."""

from __future__ import annotations


class LayoutError(Exception):
    """Base class for all domain-specific errors."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class ValidationError(LayoutError, ValueError):
    pass


class NameConflictError(LayoutError):
    pass


class UnknownEntityError(LayoutError, LookupError):
    pass


class AmbiguousNameError(LayoutError, LookupError):
    pass


class AttachmentError(LayoutError):
    pass


class ForeignLayoutError(AttachmentError):
    pass


class DanglingReferenceError(LayoutError):
    pass


class ReferenceInUseError(LayoutError):
    pass


class ReferenceCycleError(LayoutError):
    pass


class EvaluationError(LayoutError):
    pass


class StationOutOfRangeError(EvaluationError):
    pass


class NoStationSolutionError(EvaluationError):
    pass


class AmbiguousStationError(EvaluationError):
    pass
