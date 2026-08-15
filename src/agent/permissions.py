"""Agent 三级权限模型

默认批准制 → 完全访问 → 黑名单兜底
"""

import re
from typing import Optional, Tuple
from ..config import get_config


class PermissionManager:
    """权限管理器"""

    def check_tool_permission(
        self,
        tool_name: str,
        risk_level: str,
        arguments: dict,
    ) -> Tuple[bool, Optional[str]]:
        """检查工具执行权限

        Returns:
            (allowed, reason)
            allowed=True: 允许执行
            allowed=False: 需要用户确认，reason 为确认提示
        """
        config = get_config().agent_permission

        # 黑名单永远拦截（不可绕过）
        if tool_name == "run_command":
            cmd = arguments.get("command", "")
            if self._is_blacklisted(cmd, config.command_blacklist):
                return False, f"⛔ 命令被永久拦截（命令黑名单）: {cmd}"

        # 完全访问模式：安全/低危/中危/高危均放行
        if config.full_access:
            return True, None

        # 默认批准制：安全/低危直接执行，中/高危需确认
        if risk_level in ("safe", "low"):
            return True, None

        # 中危/高危需确认
        if risk_level == "medium":
            return False, self._format_confirm_medium(tool_name, arguments)
        elif risk_level == "high":
            return False, self._format_confirm_high(tool_name, arguments)

        return True, None

    def check_file_boundary(
        self,
        file_path: str,
    ) -> Tuple[bool, Optional[str]]:
        """检查文件操作边界

        Returns:
            (within_boundary, reason)
        """
        from pathlib import Path
        from ..config import BASE_DIR

        from ..config import get_agent_workdir

        config = get_config().agent_permission
        target = Path(file_path).resolve()
        base = BASE_DIR.resolve()

        # 项目目录内：直接允许
        try:
            target.relative_to(base)
            return True, None
        except ValueError:
            pass

        # Agent 工作目录内：直接允许（工作目录可位于项目之外）
        try:
            target.relative_to(get_agent_workdir())
            return True, None
        except ValueError:
            pass

        # 检查可信目录
        for trusted in config.trusted_dirs:
            try:
                target.relative_to(Path(trusted).resolve())
                return True, None
            except ValueError:
                continue

        # 越界：需要确认
        return False, f"文件操作越界项目目录: {file_path}\n是否允许访问此路径？"

    def _is_blacklisted(self, command: str, blacklist: list) -> bool:
        """检查命令是否在黑名单中"""
        cmd_lower = command.lower().strip()
        for pattern in blacklist:
            if pattern.lower() in cmd_lower:
                return True
        return False

    def _format_confirm_medium(self, tool_name: str, arguments: dict) -> str:
        """格式化中危确认提示"""
        if tool_name == "write_file":
            path = arguments.get("path", "")
            size = len(arguments.get("content", ""))
            return f"📝 写入文件确认\n路径: {path}\n大小: {size} 字符\n是否允许？"
        elif tool_name == "edit_file":
            path = arguments.get("path", "")
            old = arguments.get("old_text", "")[:100]
            return f"✏️ 编辑文件确认\n路径: {path}\n替换: {old}...\n是否允许？"
        return f"⚠️ 需要确认执行: {tool_name}"

    def _format_confirm_high(self, tool_name: str, arguments: dict) -> str:
        """格式化高危确认提示"""
        if tool_name == "run_command":
            cmd = arguments.get("command", "")
            return f"🔴 执行命令确认\n命令: {cmd}\n\n⚠️ 此操作可能产生不可逆影响，是否执行？"
        return f"🔴 高危操作确认: {tool_name}"
