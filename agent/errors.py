"""Safe, locally authored errors that may be included in incident results."""


class ModelRequestError(RuntimeError):
    """Contains actionable guidance, never a vendor response body."""
