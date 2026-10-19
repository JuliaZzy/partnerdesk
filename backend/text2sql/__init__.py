"""Natural-language questions over this partnership's rows.

The model picks tables and writes one SELECT. Code checks the statement, runs it
against views that only contain the current partnership, and returns the rows.
"""

from .text2sql import QueryResult, answer_question

__all__ = ["QueryResult", "answer_question"]
