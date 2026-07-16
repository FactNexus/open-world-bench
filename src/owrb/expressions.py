"""Safe expression evaluation for compatibility rules and derived parameters.

SPEC.md section 10.3 requires a safe evaluator rather than Python ``eval``.
Expressions are parsed with :mod:`ast` and interpreted against a restricted
node whitelist: literals, names bound to scenario parameters, attribute and
subscript access on plain data, boolean and arithmetic operators, comparisons,
conditional expressions, and a small set of pure builtin functions.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from typing import Any

_ALLOWED_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
}

_ALLOWED_CONSTANT_TYPES = (bool, int, float, str, type(None))


class ExpressionError(ValueError):
    """Raised when an expression cannot be parsed or evaluated safely."""


def parse_expression(expression: str) -> ast.Expression:
    """Parse and structurally validate an expression without evaluating it."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"invalid expression syntax: {error.msg}") from error
    _validate_node(tree.body)
    return tree


def evaluate_expression(expression: str, context: Mapping[str, Any]) -> Any:
    """Evaluate a rule or derived-parameter expression against selected parameters."""
    tree = parse_expression(expression)
    return _evaluate(tree.body, context)


def _validate_node(node: ast.expr) -> None:
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, _ALLOWED_CONSTANT_TYPES):
            raise ExpressionError(f"constant of type {type(node.value).__name__} is not allowed")
    elif isinstance(node, ast.Name):
        pass
    elif isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ExpressionError(f"attribute {node.attr!r} is not allowed")
        _validate_node(node.value)
    elif isinstance(node, ast.Subscript):
        _validate_node(node.value)
        _validate_node(node.slice)
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            _validate_node(value)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not | ast.USub | ast.UAdd):
            raise ExpressionError("unary operator is not allowed")
        _validate_node(node.operand)
    elif isinstance(node, ast.BinOp):
        if not isinstance(
            node.op, ast.Add | ast.Sub | ast.Mult | ast.Div | ast.FloorDiv | ast.Mod
        ):
            raise ExpressionError("binary operator is not allowed")
        _validate_node(node.left)
        _validate_node(node.right)
    elif isinstance(node, ast.Compare):
        for operator in node.ops:
            if not isinstance(
                operator,
                ast.Eq | ast.NotEq | ast.Lt | ast.LtE | ast.Gt | ast.GtE | ast.In | ast.NotIn,
            ):
                raise ExpressionError("comparison operator is not allowed")
        _validate_node(node.left)
        for comparator in node.comparators:
            _validate_node(comparator)
    elif isinstance(node, ast.IfExp):
        _validate_node(node.test)
        _validate_node(node.body)
        _validate_node(node.orelse)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTIONS:
            raise ExpressionError("only abs, len, max, min, and round may be called")
        if node.keywords:
            raise ExpressionError("keyword arguments are not allowed")
        for argument in node.args:
            _validate_node(argument)
    elif isinstance(node, ast.List | ast.Tuple):
        for element in node.elts:
            _validate_node(element)
    else:
        raise ExpressionError(f"expression node {type(node).__name__} is not allowed")


def _evaluate(node: ast.expr, context: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in context:
            return context[node.id]
        raise ExpressionError(f"unknown name {node.id!r}")
    if isinstance(node, ast.Attribute):
        value = _evaluate(node.value, context)
        if isinstance(value, Mapping):
            # Missing keys resolve to None so rules can reference optional
            # parameter fields such as traveller.requires_wheelchair_access.
            return value.get(node.attr)
        raise ExpressionError(f"attribute access on {type(value).__name__} is not allowed")
    if isinstance(node, ast.Subscript):
        container = _evaluate(node.value, context)
        key = _evaluate(node.slice, context)
        if isinstance(container, Mapping):
            return container.get(key)
        if isinstance(container, list | tuple | str) and isinstance(key, int):
            try:
                return container[key]
            except IndexError as error:
                raise ExpressionError(f"index {key} is out of range") from error
        raise ExpressionError(f"subscript on {type(container).__name__} is not allowed")
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for value_node in node.values:
                result = _evaluate(value_node, context)
                if not result:
                    return result
            return result
        for value_node in node.values:
            result = _evaluate(value_node, context)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, context)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, context)
        right = _evaluate(node.right, context)
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            return left % right
        except (TypeError, ZeroDivisionError) as error:
            raise ExpressionError(str(error)) from error
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, context)
        for operator, comparator_node in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator_node, context)
            if not _compare(operator, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        if _evaluate(node.test, context):
            return _evaluate(node.body, context)
        return _evaluate(node.orelse, context)
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        function = _ALLOWED_FUNCTIONS[node.func.id]
        arguments = [_evaluate(argument, context) for argument in node.args]
        try:
            return function(*arguments)
        except (TypeError, ValueError) as error:
            raise ExpressionError(str(error)) from error
    if isinstance(node, ast.List | ast.Tuple):
        return [_evaluate(element, context) for element in node.elts]
    raise ExpressionError(f"expression node {type(node).__name__} is not allowed")


def _compare(operator: ast.cmpop, left: Any, right: Any) -> bool:
    try:
        if isinstance(operator, ast.Eq):
            return bool(left == right)
        if isinstance(operator, ast.NotEq):
            return bool(left != right)
        if isinstance(operator, ast.Lt):
            return bool(left < right)
        if isinstance(operator, ast.LtE):
            return bool(left <= right)
        if isinstance(operator, ast.Gt):
            return bool(left > right)
        if isinstance(operator, ast.GtE):
            return bool(left >= right)
        if isinstance(operator, ast.In):
            return left in right
        return left not in right
    except TypeError as error:
        raise ExpressionError(str(error)) from error
