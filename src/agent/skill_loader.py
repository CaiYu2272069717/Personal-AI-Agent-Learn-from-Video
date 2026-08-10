"""
Skill 加载器模块

从指定目录扫描并加载 SKILL.md 文件，解析 skill 的元数据，
并在 Agent 运行时提供 skill 匹配和指令注入能力。

Skill 目录结构示例:
    skills/
        web-search/
            SKILL.md       # skill 定义（包含 YAML frontmatter + 指令）
            tools.py       # (可选) skill 自带的工具实现
        code-runner/
            SKILL.md
            tools.py
"""

import os
import re
import logging
import asyncio
import importlib.util
from typing import Optional, Callable, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """一个 Skill 的完整描述"""
    name: str  # skill 唯一标识（目录名）
    display_name: str  # 展示名称
    description: str  # 简要描述
    triggers: list = field(default_factory=list)  # 触发关键词列表
    instructions: str = ""  # 完整指令文本（注入到 system prompt）
    tools: list = field(default_factory=list)  # skill 自带的工具定义
    tool_functions: dict = field(default_factory=dict)  # 工具实现
    path: str = ""  # skill 目录路径


def _parse_yaml_frontmatter(content: str) -> tuple:
    """
    解析 SKILL.md 中的 YAML frontmatter

    格式:
        ---
        name: skill-name
        display_name: Skill 展示名
        description: 一句话描述
        triggers:
          - 关键词1
          - 关键词2
        ---

        实际指令内容...

    Returns:
        (metadata_dict, body_text)
    """
    metadata = {}
    body = content

    # 匹配 --- ... --- 包裹的 frontmatter
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if match:
        frontmatter_text = match.group(1)
        body = match.group(2)

        # 简单的 YAML 解析（不依赖 pyyaml）
        current_key = None
        current_list = None

        for line in frontmatter_text.split("\n"):
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue

            # 列表项: "  - value"
            list_match = re.match(r"^\s+-\s+(.+)", line)
            if list_match and current_key:
                if current_list is None:
                    current_list = []
                    metadata[current_key] = current_list
                current_list.append(list_match.group(1).strip())
                continue

            # 键值对: "key: value"
            kv_match = re.match(r"^(\w[\w_-]*):\s*(.*)", line)
            if kv_match:
                current_key = kv_match.group(1)
                value = kv_match.group(2).strip()
                current_list = None
                if value:
                    metadata[current_key] = value
                continue

    return metadata, body


def _load_skill_tools(skill_dir: str) -> tuple:
    """
    加载 skill 目录下的 tools.py 中定义的工具

    tools.py 需要导出:
    - TOOLS: list[dict]  # OpenAI function calling 格式的工具定义
    - 以及对应的函数实现

    Returns:
        (tool_definitions, tool_functions)
    """
    tools_file = os.path.join(skill_dir, "tools.py")
    if not os.path.exists(tools_file):
        return [], {}

    try:
        # 动态加载模块
        spec = importlib.util.spec_from_file_location(
            f"skill_tools_{os.path.basename(skill_dir)}",
            tools_file
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 获取工具定义
        tool_defs = getattr(module, "TOOLS", [])

        # 获取工具函数（与定义中的 name 对应）
        tool_funcs = {}
        for tool_def in tool_defs:
            func_name = tool_def.get("function", {}).get("name", "")
            if func_name and hasattr(module, func_name):
                tool_funcs[func_name] = getattr(module, func_name)

        return tool_defs, tool_funcs

    except Exception as e:
        logger.error(f"加载 skill 工具失败 [{skill_dir}]: {e}")
        return [], {}


class SkillManager:
    """
    Skill 管理器 —— 负责加载、匹配、调用 Skill

    功能：
    - 从目录扫描加载所有 Skill
    - 根据用户输入匹配相关 Skill
    - 将匹配的 Skill 指令注入到 Agent 上下文
    - 调用 Skill 自带的工具
    """

    def __init__(self):
        self._skills: dict = {}

    def load_from_directory(self, skills_dir: str) -> int:
        """
        从目录加载所有 Skill

        Args:
            skills_dir: skills/ 目录路径

        Returns:
            成功加载的 skill 数量
        """
        if not os.path.isdir(skills_dir):
            logger.info(f"Skill 目录不存在: {skills_dir}，跳过加载")
            return 0

        loaded = 0
        for entry in os.listdir(skills_dir):
            skill_dir = os.path.join(skills_dir, entry)
            skill_file = os.path.join(skill_dir, "SKILL.md")

            if not os.path.isfile(skill_file):
                continue

            skill = self._load_single_skill(skill_dir, skill_file)
            if skill:
                self._skills[skill.name] = skill
                loaded += 1
                logger.info(f"已加载 Skill: {skill.display_name} ({skill.name})")

        logger.info(f"Skill 管理器: 已加载 {loaded} 个 Skill")
        return loaded

    def _load_single_skill(self, skill_dir: str, skill_file: str) -> Optional[Skill]:
        """加载单个 Skill"""
        try:
            with open(skill_file, "r", encoding="utf-8") as f:
                content = f.read()

            metadata, body = _parse_yaml_frontmatter(content)

            # 加载 skill 自带的工具
            tool_defs, tool_funcs = _load_skill_tools(skill_dir)

            skill = Skill(
                name=metadata.get("name", os.path.basename(skill_dir)),
                display_name=metadata.get("display_name",
                                          metadata.get("name",
                                                       os.path.basename(skill_dir))),
                description=metadata.get("description", ""),
                triggers=metadata.get("triggers", []),
                instructions=body.strip(),
                tools=tool_defs,
                tool_functions=tool_funcs,
                path=skill_dir,
            )
            return skill

        except Exception as e:
            logger.error(f"加载 Skill 失败 [{skill_dir}]: {e}")
            return None

    def match_skills(self, user_input: str) -> list:
        """
        根据用户输入匹配相关的 Skill

        匹配逻辑：
        1. 用户输入中包含 skill 的触发关键词
        2. 用户输入中包含 skill 的名称

        Args:
            user_input: 用户输入文本

        Returns:
            匹配到的 Skill 列表（按相关度排序）
        """
        matched = []
        input_lower = user_input.lower()

        for skill in self._skills.values():
            score = 0

            # 检查触发关键词
            for trigger in skill.triggers:
                if trigger.lower() in input_lower:
                    score += 2

            # 检查 skill 名称
            if skill.name.lower() in input_lower:
                score += 1

            if score > 0:
                matched.append((score, skill))

        # 按分数降序排列
        matched.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in matched]

    def get_skill(self, name: str) -> Optional[Skill]:
        """按名称获取 Skill"""
        return self._skills.get(name)

    def list_skills(self) -> list:
        """列出所有已加载的 Skill"""
        return list(self._skills.values())

    def get_skill_tools_openai_format(self) -> list:
        """
        获取所有 Skill 自带工具的 OpenAI function calling 格式定义

        Returns:
            工具定义列表
        """
        result = []
        for skill in self._skills.values():
            for tool_def in skill.tools:
                # 深拷贝避免修改原始定义
                import copy
                prefixed_def = copy.deepcopy(tool_def)
                func = prefixed_def.get("function", {})
                original_name = func.get("name", "")
                func["name"] = f"skill__{skill.name}__{original_name}"
                func["description"] = (
                    f"[Skill:{skill.display_name}] "
                    f"{func.get('description', '')}"
                )
                result.append(prefixed_def)
        return result

    async def call_skill_tool(self, full_name: str, arguments: dict) -> Any:
        """
        调用 Skill 自带的工具

        Args:
            full_name: 完整工具名，格式为 skill__<skill_name>__<tool_name>
            arguments: 工具参数

        Returns:
            工具调用结果
        """
        # 解析: skill__<skill_name>__<tool_name>
        parts = full_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "skill":
            return {"error": f"无效的 Skill 工具名: {full_name}"}

        skill_name = parts[1]
        tool_name = parts[2]

        skill = self._skills.get(skill_name)
        if not skill:
            return {"error": f"Skill {skill_name} 未找到"}

        func = skill.tool_functions.get(tool_name)
        if not func:
            return {"error": f"Skill {skill_name} 中未找到工具 {tool_name}"}

        try:
            # 支持同步和异步函数
            if asyncio.iscoroutinefunction(func):
                return await func(**arguments)
            else:
                return func(**arguments)
        except Exception as e:
            return {"error": f"调用 Skill 工具失败: {e}"}

    def is_skill_tool(self, tool_name: str) -> bool:
        """判断一个工具名是否是 Skill 工具"""
        return tool_name.startswith("skill__")

    def get_all_tool_defs(self) -> list:
        """获取所有 Skill 工具的 OpenAI function calling 格式定义（供 ToolRegistry 使用）"""
        return self.get_skill_tools_openai_format()

    async def call_tool(self, full_name: str, arguments: dict) -> str:
        """调用 Skill 工具（供 ToolRegistry.execute 使用）"""
        import json as _json
        result = await self.call_skill_tool(full_name, arguments)
        if isinstance(result, str):
            return result
        return _json.dumps(result, ensure_ascii=False, default=str)

    def build_skill_context(self, matched_skills: list) -> str:
        """
        将匹配的 Skill 指令构建为上下文文本，注入到 system prompt

        Args:
            matched_skills: 匹配到的 Skill 列表

        Returns:
            格式化后的上下文字符串
        """
        if not matched_skills:
            return ""

        sections = []
        for skill in matched_skills:
            section = (
                f"\n<skill name=\"{skill.name}\">\n"
                f"{skill.instructions}\n"
                f"</skill>\n"
            )
            sections.append(section)

        return (
            "\n<active_skills>\n"
            "以下 Skill 已被激活，请遵循其指令：\n"
            + "".join(sections)
            + "</active_skills>\n"
        )
