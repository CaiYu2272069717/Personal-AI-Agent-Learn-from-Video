"""
代码执行 Skill 的工具实现
"""

import sys
import io
import traceback

# OpenAI function calling 格式的工具定义
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "安全执行 Python 代码片段并返回输出结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码"
                    }
                },
                "required": ["code"]
            }
        }
    }
]


def run_python(code: str) -> str:
    """
    在沙箱环境中执行 Python 代码
    
    Args:
        code: 要执行的 Python 代码
        
    Returns:
        代码执行的标准输出，或错误信息
    """
    # 安全检查：禁止危险操作
    dangerous_keywords = [
        "os.system", "subprocess", "shutil.rmtree",
        "os.remove", "os.rmdir", "__import__",
        "eval(", "exec(", "open(",
    ]
    for keyword in dangerous_keywords:
        if keyword in code:
            return f"安全限制：代码包含危险操作 '{keyword}'，禁止执行。"

    # 捕获标准输出
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        # 使用受限的全局命名空间
        safe_globals = {
            "__builtins__": {
                "print": print,
                "len": len,
                "range": range,
                "int": int,
                "float": float,
                "str": str,
                "list": list,
                "dict": dict,
                "tuple": tuple,
                "set": set,
                "bool": bool,
                "sum": sum,
                "max": max,
                "min": min,
                "abs": abs,
                "round": round,
                "sorted": sorted,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
                "type": type,
                "isinstance": isinstance,
            }
        }

        exec(code, safe_globals)

        output = sys.stdout.getvalue()
        errors = sys.stderr.getvalue()

        result = ""
        if output:
            result += output
        if errors:
            result += f"\n[stderr]:\n{errors}"
        if not result:
            result = "(代码执行完成，无输出)"

        return result

    except Exception as e:
        return f"执行错误:\n{traceback.format_exc()}"

    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
