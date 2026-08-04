"""受限 Python 子进程入口。代码通过 stdin 输入。"""

import ast
import json
import math
import statistics
import sys


ALLOWED_NODES = {
    ast.Module, ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple,
    ast.Set, ast.Dict, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.If, ast.IfExp, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass,
    ast.Call, ast.keyword, ast.Attribute, ast.Subscript, ast.Slice,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
    ast.JoinedStr, ast.FormattedValue,
}

SAFE_BUILTINS = {
    "print": print, "len": len, "range": range, "enumerate": enumerate,
    "zip": zip, "sorted": sorted, "reversed": reversed,
    "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "all": all, "any": any,
    "str": str, "int": int, "float": float, "bool": bool,
    "list": list, "tuple": tuple, "dict": dict, "set": set,
}


def validate(tree: ast.AST):
    for node in ast.walk(tree):
        if type(node) not in ALLOWED_NODES:
            raise ValueError(f"不允许的语法: {type(node).__name__}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("不允许访问私有属性")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("不允许访问特殊名称")


def main():
    source = sys.stdin.read()
    if len(source) > 50_000:
        raise ValueError("代码长度超过 50000 字符")
    tree = ast.parse(source, mode="exec")
    validate(tree)
    globals_dict = {
        "__builtins__": SAFE_BUILTINS,
        "math": math,
        "statistics": statistics,
        "json": json,
    }
    exec(compile(tree, "<agent-sandbox>", "exec"), globals_dict, globals_dict)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SandboxError: {exc}", file=sys.stderr)
        raise SystemExit(1)
