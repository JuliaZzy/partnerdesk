"""Shared retry when a model writes something the process then runs.

SQL, chart JSON, and the report's analyze() all call this. Three runs, then the
last artifact and its error are handed to a person.
"""

from .fix import format_failures, with_reflection

__all__ = ["format_failures", "with_reflection"]
