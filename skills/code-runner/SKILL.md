---
name: code-runner
display_name: 代码执行
description: 在安全沙箱中执行 Python 代码并返回结果
triggers:
  - 运行代码
  - 执行代码
  - 计算
  - python
---

# 代码执行 Skill

当用户需要执行计算、数据处理或验证代码逻辑时，使用沙箱工具执行 Python 代码。

## 使用规则

1. 仅支持安全的计算和文本处理操作
2. 禁止文件 I/O、网络请求、进程操作
3. 返回代码的标准输出作为结果
4. 超时限制 15 秒
