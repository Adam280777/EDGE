"""Errors that can be presented to users without hiding the failure."""


class EdgeError(Exception):
    """A failed validation, simulation or persistence operation."""


class ValidationError(EdgeError):
    """Input is unsuitable for this simulation milestone."""
