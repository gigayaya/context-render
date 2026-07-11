"""Exit-code-3 precondition/environment errors (A3.1)."""


class PreconditionError(Exception):
    """Not init'd, transcripts not found, DB corrupt/schema mismatch, config invalid → exit 3."""
