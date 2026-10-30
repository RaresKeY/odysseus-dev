"""One permission-filtered selection shared by preprocessing and both loops."""
from __future__ import annotations

from dataclasses import replace
import json

from src.tool_policy import build_effective_tool_policy, known_tool_names, WEB_ACCESS_TOOL_NAMES
from src.turn_contract import canonical_tool


def selection_for(bound):
    selection = getattr(bound, "tool_selection", None) if bound else None
    return selection if selection is not None and selection.kind != "existing" else None


def selected_policy(policy, bound):
    selection = selection_for(bound)
    if selection is None:
        return policy
    selection.validate(bound)
    allowed = {canonical_tool(n) for n in selection.selected_names}
    omitted = {n for n in known_tool_names() if canonical_tool(n) not in allowed}
    return replace(policy, disabled_tools=frozenset(set(policy.disabled_tools) | omitted),
        hidden_tools=frozenset(set(policy.hidden_tools) | omitted),
        mode="guide_only" if selection.kind == "no_tools" else policy.mode,
        block_all_tool_calls=policy.block_all_tool_calls or selection.kind == "no_tools")


async def prepare_http_selection(request, session, message, *, agent_mode, policy,
                                 allow_bash=None, web_enabled=False, plan_mode=False,
                                 incognito=False, compare_mode=False, workspace=None,
                                 active_email=False):
    from src.chat_automation import current_turn
    bound = current_turn()
    if bound is None or bound.configuration.get("tool_routing", "existing") == "existing":
        return None
    # A plain answer route must not acquire an agent tool surface. Its explicit
    # pre-search remains owned by that route, not a generic tool classifier.
    if not agent_mode:
        return None
    from routes.chat_routes import _request_privileges
    from src.auth_helpers import is_delegated_credential
    from src.tool_security import blocked_tools_for_owner, plan_mode_disabled_tools, delegated_credential_blocked_tools
    from src.settings import get_setting
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_utils import get_mcp_manager
    from src.agent_loop import _load_mcp_disabled_map
    disabled = set(policy.all_disabled_names()) | set(blocked_tools_for_owner(bound.owner))
    admin_disabled = get_setting("disabled_tools", [])
    if isinstance(admin_disabled, list):
        disabled.update(admin_disabled)
    if is_delegated_credential(request):
        disabled.update(delegated_credential_blocked_tools())
    if allow_bash is not None and str(allow_bash).lower() != "true":
        disabled.add("bash")
    if not web_enabled:
        disabled.update(WEB_ACCESS_TOOL_NAMES)
    if plan_mode:
        disabled.update(plan_mode_disabled_tools())
    privileges = _request_privileges(request, bound.owner) or {}
    from src.turn_contract import FAMILY_TOOLS
    for flag, names in {
        "can_use_bash": FAMILY_TOOLS["shell_files"],
        "can_use_browser": {"private_browser", "builtin_browser"},
        "can_use_documents": FAMILY_TOOLS["documents"],
        "can_generate_images": {"generate_image", "edit_image"},
        "can_manage_memory": {"manage_memory", "manage_skills"},
        "can_use_research": {"trigger_research", "manage_research"},
    }.items():
        if not privileges.get(flag, True):
            disabled.update(names)
    if incognito:
        disabled.update({"manage_memory", "search_chats", "manage_skills", "create_session",
                         "list_sessions", "manage_session", "send_to_session", "chat_with_model"})
    if compare_mode:
        disabled.update({"create_document", "edit_document", "update_document", "chat_with_model",
            "create_session", "list_sessions", "send_to_session", "pipeline", "manage_session",
            "manage_memory", "list_models", "generate_image", "ui_control"})
    if active_email:
        disabled.update({"create_document", "send_email", "reply_to_email"})
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] != "host_shell"]
    manager = get_mcp_manager()
    if manager and not plan_mode and not policy.disable_mcp:
        schemas.extend(manager.get_all_openai_schemas(_load_mcp_disabled_map()))
    permission = build_effective_tool_policy(disabled_tools=disabled, last_user_message=message)
    denied = {canonical_tool(n) for n in disabled}
    schemas = [s for s in schemas if not permission.blocks(s["function"]["name"])
        and canonical_tool(s["function"]["name"]) not in denied]
    if not privileges.get("can_use_agent", True):
        schemas = []
    # Exact immutable JSON copies; caller/model dictionaries cannot mutate the
    # catalog after the decision. Full content/ask overflow is visibly rejected.
    inventory = json.loads(json.dumps(schemas, allow_nan=False))
    from src.decision_routing import prepare_tool_routing
    await prepare_tool_routing(bound, inventory)
    bound._tool_routing_inventory = inventory
    return selection_for(bound)


def selected_contract(contract, bound, policy):
    """Reproject executable schemas without lexical or warm-tool expansion."""
    selection = selection_for(bound)
    if selection is None or contract is None:
        return contract
    inventory = getattr(bound, "_tool_routing_inventory", None)
    selection.validate(bound, inventory=inventory)
    allowed = {canonical_tool(n) for n in selection.selected_names}
    schemas = {s["function"]["name"]: s for s in inventory or []
               if canonical_tool(s["function"]["name"]) in allowed
               and not policy.blocks(s["function"]["name"])}
    offered = frozenset(schemas)
    required = frozenset(n for n in contract.required if canonical_tool(n) in allowed and n in offered)
    missing = frozenset(canonical_tool(n) for n in contract.required - required)
    # A trusted required operation conflicting with no_tools is a visible
    # conflict, not an excuse to restore it or silently perform partial work.
    if missing:
        offered, required, schemas = frozenset(), frozenset(), {}
        if contract.required_read_operation is not None:
            missing |= {canonical_tool(contract.required_read_operation.tool)}
    return replace(contract, required=required, offered=offered,
        executable=frozenset(n for n in contract.executable | offered if canonical_tool(n) in allowed),
        unavailable=contract.unavailable | missing,
        schema_json=tuple(json.dumps(schemas[n], sort_keys=True) for n in sorted(schemas)))
