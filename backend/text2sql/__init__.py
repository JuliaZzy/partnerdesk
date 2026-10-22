"""用自然语言问这个合作关系的行。

模型选表并写一条 SELECT。代码检查语句，在只含当前合作关系的视图上执行，然后返回行。
"""

from .text2sql import QueryResult, answer_question, run_statement

__all__ = ["QueryResult", "answer_question", "run_statement"]
