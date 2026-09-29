"""工具 spec 与提示词的一致性测试。

对应 codex 的 `*_spec_tests.rs`:工具**面向模型的说明**(schema + 描述 + 提示词引导)
必须与工具的**实际装配情况**一致。这里把四类"曾经只能靠人记住"的漂移变成失败断言:

1. 工具加了没登记 spec / spec 指向已删除的工具 / 组名写错;
2. 参数说明缺失,或 spec 的参数键与工具 schema 对不上;
3. 参数说明没真正进入**请求体用的 schema**(只写进本地对象等于没写);
4. **提示词里出现的工具 ≠ 实际装配的工具**(写了却调不到,会直接诱发模型"用正文粘贴实现")。

另有第 1 项(提示词硬规则)的断言:规则必须真的在提示词里,且位置正确。

运行:python tests/test_tool_specs.py(或 pytest tests/)
"""
from __future__ import annotations

import json

from _harness import BACKEND_ROOT, CONTEXT_MATRIX, PERMISSIONS, run, tool_context

from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent.manager import plan_mode_hidden_tools
from app.agent.prompts import build_system_prompt, permission_tool_names, rendered_tool_names
from app.tools import specs
from app.tools.permissions import PERMISSION_FULL_ACCESS, PERMISSION_READ_ONLY
from app.tools.registry import registry
from app.tools.specs import group_label

FULL = PERMISSIONS[2]


def test_specs_and_registry_agree() -> None:
    """spec 与注册表一一对应(工具没登记 spec、spec 指向不存在的工具、组名写错)。

    这条与 `tools/registry.py` 导入时的启动校验是同一份判断,这里再断一次是为了
    在任何导入路径下都有明确失败信息。
    """
    problems = specs.validate_against(registry)
    assert problems == [], problems


def test_spec_param_keys_match_tool_schema() -> None:
    """spec 里写的参数键必须与工具 `args_schema` 的字段一一对应。"""
    problems: list[str] = []
    for permission, plan_mode, computer in CONTEXT_MATRIX:
        ctx = tool_context(permission, plan_mode=plan_mode, computer=computer)
        for tool in registry.build(ctx):
            spec = specs.spec_for(tool.name)
            if spec is None:
                problems.append(f"{tool.name}: 没有 spec")
                continue
            keys = set(spec.param_docs())
            if not keys:
                continue  # 参数说明由工具自己的 schema 提供(如 computer)
            fields = set(getattr(tool.args_schema, "model_fields", {}) or {})
            if keys != fields:
                problems.append(
                    f"{tool.name}({permission},plan={plan_mode}): "
                    f"spec={sorted(keys)} != schema={sorted(fields)}"
                )
    assert problems == [], "\n".join(sorted(set(problems)))


def test_every_param_is_documented() -> None:
    """每个工具的参数都要有说明(来自 spec,或工具自带 schema)。"""
    gaps: list[str] = []
    for permission, plan_mode, computer in CONTEXT_MATRIX:
        ctx = tool_context(permission, plan_mode=plan_mode, computer=computer)
        for tool in registry.build(ctx):
            fields = getattr(tool.args_schema, "model_fields", {}) or {}
            if not fields:
                continue
            missing = [name for name, info in fields.items() if not info.description]
            if missing:
                gaps.append(f"{tool.name}({permission},plan={plan_mode}): {sorted(missing)}")
    assert gaps == [], "参数缺说明(请在 app/tools/specs.py 补 params):\n" + "\n".join(sorted(set(gaps)))


def test_param_docs_reach_model_visible_schema() -> None:
    """参数说明要出现在**发给模型的那份 schema**里(convert_to_openai_tool)。"""
    ctx = tool_context(FULL, computer=True)
    tools = {tool.name: tool for tool in registry.build(ctx)}
    edit = convert_to_openai_tool(tools["edit_file"])
    props = edit["function"]["parameters"]["properties"]
    for name in ("path", "old_string", "new_string", "replace_all"):
        assert props[name].get("description"), f"edit_file.{name} 在请求体 schema 里没有说明"
    # 内容确实来自 spec(单一来源),而不是别处又写了一份
    spec_docs = specs.spec_for("edit_file").param_docs()
    assert props["replace_all"]["description"] == spec_docs["replace_all"]


def test_rebuilt_schema_keeps_constraints() -> None:
    """重建 schema 只为加一句说明,不能把原有约束弄丢(否则等于悄悄放宽校验)。"""
    ctx = tool_context(PERMISSIONS[0], plan_mode=True)
    tools = {tool.name: tool for tool in registry.build(ctx)}
    items = tools["ask_user"].args_schema.model_json_schema()["properties"]["questions"]
    assert items.get("minItems") == 1, items
    assert items.get("maxItems") == 4, items


def test_tool_still_invokes_after_schema_rebuild() -> None:
    """加说明不能影响调用:真的执行一次只读工具(参数校验仍能通过)。"""
    ctx = tool_context(PERMISSIONS[0])
    tools = {tool.name: tool for tool in registry.build(ctx)}
    payload = json.loads(tools["list_dir"].invoke({"path": "."}))
    assert payload["status"] == "ok", payload
    assert any(entry["name"] == "app" for entry in payload["entries"]), payload


def test_prompt_tools_match_assembled_tools() -> None:
    """不变量:提示词里出现的工具 == 实际装配的工具(权限 × 计划模式 × 开关 全组合)。"""
    problems: list[str] = []
    for permission, plan_mode, computer in CONTEXT_MATRIX:
        ctx = tool_context(permission, plan_mode=plan_mode, computer=computer)
        hidden = plan_mode_hidden_tools(plan_mode)
        built = {tool.name for tool in registry.build(ctx) if tool.name not in hidden}
        computer_available = computer and permission == PERMISSION_FULL_ACCESS and not plan_mode
        rendered = set(
            rendered_tool_names(
                plan_mode=plan_mode, hidden_tools=hidden, computer_available=computer_available
            )
        )
        if built != rendered:
            problems.append(
                f"({permission},plan={plan_mode},computer={computer}): "
                f"只在提示词里={sorted(rendered - built)} 只装配了={sorted(built - rendered)}"
            )
    assert problems == [], "\n".join(problems)


def test_prompt_lists_every_rendered_tool() -> None:
    """提示词里每个应当出现的工具都要真的有一行引导。"""
    prompt = build_system_prompt(
        "test-model", FULL, str(BACKEND_ROOT), computer_use_enabled=True
    )
    for name in rendered_tool_names(computer_available=True):
        assert f"- {name}: " in prompt, f"提示词缺少工具引导:{name}"


def test_safety_rules_are_in_the_prompt() -> None:
    """第 1 项:硬规则必须在提示词里,且位置在「能力」之后、「工具引导」之前。"""
    keywords = (
        "# 安全与停止条件",
        "先停下问用户",
        "git reset --hard",
        "不要原样重试",
        "不确定就问",
    )
    for plan_mode in (False, True):
        prompt = build_system_prompt(
            "test-model", FULL, str(BACKEND_ROOT), plan_mode, computer_use_enabled=True
        )
        for keyword in keywords:
            assert keyword in prompt, f"plan_mode={plan_mode} 时提示词缺少硬规则:{keyword}"
        assert prompt.index("# 安全与停止条件") < prompt.index("# 工具引导")
        assert prompt.index("# 能力") < prompt.index("# 安全与停止条件")


def test_plan_only_guidance_follows_plan_mode() -> None:
    """计划模式专用工具的引导只在计划模式下出现(反之亦然)。

    这里按 manager 的真实调用方式传 `hidden_tools` —— 计划模式下写类工具是被
    **摘掉**的(不是提示词里写一句"别动手"),引导必须随之消失。
    """
    off = build_system_prompt(
        "m", FULL, str(BACKEND_ROOT), False, plan_mode_hidden_tools(False)
    )
    on = build_system_prompt("m", FULL, str(BACKEND_ROOT), True, plan_mode_hidden_tools(True))
    assert "- exit_plan_mode: " not in off and "- ask_user: " not in off
    assert "- exit_plan_mode: " in on and "- ask_user: " in on
    # 计划模式下写类工具的引导必须消失(工具已被摘掉)
    assert "- write_file: " not in on and "- run_shell_command: " not in on


def test_permission_section_lists_the_real_tool_set() -> None:
    """权限边界里列的工具必须与实际装配一致(工具名不再手写,改工具名不会再静默说谎)。"""
    problems: list[str] = []
    for permission in PERMISSIONS:
        for computer in (False, True):
            # computer_available 的判定与 manager/prompts 一致:开关打开 + 全部权限 + 非计划模式
            computer_available = computer and permission == PERMISSION_FULL_ACCESS
            usable, denied = permission_tool_names(permission, computer_available=computer_available)
            built = {tool.name for tool in registry.build(tool_context(permission, computer=computer))}
            if set(usable) | set(denied) != built:
                problems.append(
                    f"({permission},computer={computer}): "
                    f"文案={sorted(set(usable) | set(denied))} 装配={sorted(built)}"
                )
            overlap = set(usable) & set(denied)
            if overlap:
                problems.append(f"({permission},computer={computer}): 同时列在可用与禁用 {sorted(overlap)}")
    assert problems == [], "\n".join(problems)


def test_group_labels_cover_every_tool() -> None:
    """能力文案不能出现"组名原文"(出现就说明分组名没登记,会渲染成 code_exec 这种)。"""
    gaps = [
        spec.name
        for spec in specs.SPECS
        if group_label(spec.group, needs_write=spec.needs_write) == spec.group
    ]
    assert gaps == [], f"以下工具的能力分组名未登记(app/tools/specs.py): {gaps}"


def test_needs_write_declaration_matches_tool_behavior() -> None:
    """specs 声明的 needs_write 必须与工具**实际行为**一致:只读档位真的会被拒。

    这条同时替代了"读注释猜哪个工具要写权限":声明与门控不符时测试直接失败。
    """
    tools = {tool.name: tool for tool in registry.build(tool_context(PERMISSION_READ_ONLY))}
    args_for = {
        "write_file": {"path": "x.txt", "content": "x"},
        "edit_file": {"path": "x.txt", "old_string": "a", "new_string": "b"},
        "run_shell_command": {"command": "echo hi"},
    }
    for name in sorted(specs.needs_write_names()):
        payload = json.loads(tools[name].invoke(args_for[name]))
        assert payload["status"] == "error", (name, payload)
        assert "只读" in payload["error"], (name, payload)
    # 反例:不声明 needs_write 的工具在只读档位确实能用(绘图工具真的执行一次并存图)
    ok = json.loads(
        tools["run_python_code"].invoke(
            {"code": "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.savefig('retention-test.png')"}
        )
    )
    assert ok["status"] == "ok", ok


def test_default_hidden_tools_follows_plan_mode() -> None:
    """不传 hidden_tools 时也要按同一规则裁剪(否则提示词会"看起来正常但在说谎")。"""
    implicit = build_system_prompt(
        "m", FULL, str(BACKEND_ROOT), True, computer_use_enabled=True
    )
    explicit = build_system_prompt(
        "m", FULL, str(BACKEND_ROOT), True, plan_mode_hidden_tools(True), computer_use_enabled=True
    )
    assert implicit == explicit
    for name in sorted(plan_mode_hidden_tools(True)):
        assert f"- {name}: " not in implicit, f"计划模式仍列出写类工具引导:{name}"
        assert name in implicit  # 但要在"已被摘掉"的说明里点到名


if __name__ == "__main__":
    raise SystemExit(run(globals()))
