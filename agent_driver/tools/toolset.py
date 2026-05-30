"""ToolSet helpers for selecting model-visible and executable tool surface."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.contracts.enums import AgentProfile, SideEffectClass, ToolRisk
from agent_driver.contracts.tools import ToolManifest
from agent_driver.tools.registry import ToolRegistry

_RISK_RANK = {
    ToolRisk.LOW: 0,
    ToolRisk.MEDIUM: 1,
    ToolRisk.HIGH: 2,
}

_BUILTIN_PACKS: dict[str, tuple[str, ...]] = {
    "filesystem_read": ("read_file", "glob_search", "grep_search"),
    "filesystem_write": ("file_write", "file_edit", "notebook_edit"),
    "web": ("web_fetch", "web_search"),
    "shell": ("bash", "powershell_tool"),
    "python_exec": ("python",),
    "code_intelligence": ("lsp_tool",),
    "planning_progress": (
        "planning_state_update",
        "todo_write",
        "ask_user_question",
    ),
    "planning_approval": (
        "enter_plan_mode",
        "exit_plan_mode_v2",
    ),
    "planning": (
        "planning_state_update",
        "todo_write",
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode_v2",
    ),
    "tasking": (
        "task_create",
        "task_get",
        "task_list",
        "task_update",
        "task_output",
        "task_stop_tool",
        "monitor_tool",
        "sleep_tool",
    ),
    "mcp": ("mcp_tool", "mcp_list_resources", "mcp_read_resource"),
    "worktree": ("enter_worktree_tool", "exit_worktree_tool"),
    "automation": (
        "workflow_tool",
        "cron_create_tool",
        "cron_delete_tool",
        "cron_list_tool",
        "remote_trigger_tool",
        "subscribe_pr_tool",
        "push_notification_tool",
        "send_user_file_tool",
    ),
    "discovery": (
        "skill_tool",
        "tool_search",
        "brief_tool",
        "agent_tool",
        "send_message_tool",
        "list_peers_tool",
        "team_create_tool",
        "team_delete_tool",
        "team_get_tool",
        "team_list_tool",
    ),
}


@dataclass(frozen=True, slots=True)
class ToolSet:
    """Declarative tool-surface selector over an existing registry."""

    names: tuple[str, ...] | None = None
    excluded_names: tuple[str, ...] | None = None
    max_risk: ToolRisk | None = None
    side_effects: tuple[SideEffectClass, ...] | None = None
    profile: AgentProfile | None = None
    application_tags: tuple[str, ...] | None = None

    @classmethod
    def all(cls) -> "ToolSet":
        """Include all tools from source registry."""
        return cls()

    @classmethod
    def only(cls, *names: str) -> "ToolSet":
        """Include exactly explicit tool names."""
        return cls(
            names=tuple(dict.fromkeys(item.strip() for item in names if item.strip()))
        )

    @classmethod
    def packs(cls, *pack_names: str) -> "ToolSet":
        """Compose one or more named built-in packs."""
        selected: list[str] = []
        for pack_name in pack_names:
            if pack_name not in _BUILTIN_PACKS:
                raise ValueError(f"unknown tool pack '{pack_name}'")
            selected.extend(_BUILTIN_PACKS[pack_name])
        return cls.only(*selected)

    @classmethod
    def from_preset(cls, name: str) -> "ToolSet":
        """Return a ToolSet matching one of the standard governance presets.

        Presets are coarse-grained tool surfaces intended for UI / config
        layers that do not want to compose risk + side-effect + pack filters
        from scratch. Custom selections should still use the builder API
        (``only()`` / ``packs()`` / ``with_*()``) for finer control.

        Supported names:

        * ``"off"``  — no tools at all. Useful for chat-only or planning-only
          contexts where the model must answer without invoking anything.
        * ``"safe"`` — LOW-risk read-only / inspection tools. Tools must
          have ``risk=LOW`` **and** ``side_effect`` in ``{NONE, READ_ONLY}``.
          Suitable for untrusted operators or restricted demo modes.
        * ``"dev"``  — LOW + MEDIUM risk; excludes EXTERNAL_ACTION /
          IRREVERSIBLE_WRITE side effects (no production-mutating tools).
          Suitable for dev consoles where mistakes should be reversible.
        * ``"all"``  — no filter (same as ``ToolSet.all()``). Suitable for
          trusted operators in production engagements.

        Raises ``ValueError`` for unknown preset names so misconfigured UIs
        fail loudly rather than silently selecting an empty surface.
        """
        key = (name or "").strip().lower()
        if key == "off":
            # Filter that matches nothing: empty explicit names tuple. ``names=()``
            # is treated by ``_name_matches`` as "no name passes the filter",
            # so the resulting registry is empty regardless of source contents.
            return cls(names=())
        if key == "safe":
            return cls(
                max_risk=ToolRisk.LOW,
                side_effects=(SideEffectClass.NONE, SideEffectClass.READ_ONLY),
            )
        if key == "dev":
            return cls(
                max_risk=ToolRisk.MEDIUM,
                side_effects=(
                    SideEffectClass.NONE,
                    SideEffectClass.READ_ONLY,
                    SideEffectClass.REVERSIBLE_WRITE,
                ),
            )
        if key == "all":
            return cls.all()
        raise ValueError(
            f"unknown ToolSet preset '{name}'; expected one of off, safe, dev, all"
        )

    def with_max_risk(self, max_risk: ToolRisk) -> "ToolSet":
        """Return copy capped by maximum risk."""
        return ToolSet(
            names=self.names,
            excluded_names=self.excluded_names,
            max_risk=max_risk,
            side_effects=self.side_effects,
            profile=self.profile,
            application_tags=self.application_tags,
        )

    def with_profile(self, profile: AgentProfile) -> "ToolSet":
        """Return copy constrained by agent profile compatibility."""
        return ToolSet(
            names=self.names,
            excluded_names=self.excluded_names,
            max_risk=self.max_risk,
            side_effects=self.side_effects,
            profile=profile,
            application_tags=self.application_tags,
        )

    def with_side_effects(self, *side_effects: SideEffectClass) -> "ToolSet":
        """Return copy constrained to selected side-effect classes."""
        unique = tuple(dict.fromkeys(side_effects))
        next_side_effects: tuple[SideEffectClass, ...] | None = (
            unique if unique else None
        )
        return ToolSet(
            names=self.names,
            excluded_names=self.excluded_names,
            max_risk=self.max_risk,
            side_effects=next_side_effects,
            profile=self.profile,
            application_tags=self.application_tags,
        )

    def with_application_tags(self, *tags: str) -> "ToolSet":
        """Return copy constrained by manifest application tags."""
        normalized = tuple(dict.fromkeys(item.strip() for item in tags if item.strip()))
        next_tags: tuple[str, ...] | None = normalized if normalized else None
        return ToolSet(
            names=self.names,
            excluded_names=self.excluded_names,
            max_risk=self.max_risk,
            side_effects=self.side_effects,
            profile=self.profile,
            application_tags=next_tags,
        )

    def without(self, *names: str) -> "ToolSet":
        """Return copy excluding explicit tool names."""
        existing = set(self.excluded_names or ())
        existing.update(item.strip() for item in names if item.strip())
        return ToolSet(
            names=self.names,
            excluded_names=tuple(sorted(existing)),
            max_risk=self.max_risk,
            side_effects=self.side_effects,
            profile=self.profile,
            application_tags=self.application_tags,
        )

    def _matches(self, manifest: ToolManifest) -> bool:
        checks = (
            self._name_matches(manifest),
            self._risk_matches(manifest),
            self._side_effect_matches(manifest),
            self._profile_matches(manifest),
            self._application_tags_match(manifest),
        )
        return all(checks)

    def _name_matches(self, manifest: ToolManifest) -> bool:
        if self.names is None:
            return manifest.name not in set(self.excluded_names or ())
        return manifest.name in set(self.names) and manifest.name not in set(
            self.excluded_names or ()
        )

    def _risk_matches(self, manifest: ToolManifest) -> bool:
        if self.max_risk is None:
            return True
        return _RISK_RANK[manifest.risk] <= _RISK_RANK[self.max_risk]

    def _side_effect_matches(self, manifest: ToolManifest) -> bool:
        if self.side_effects is None:
            return True
        return manifest.side_effect in set(self.side_effects)

    def _profile_matches(self, manifest: ToolManifest) -> bool:
        if self.profile is None:
            return True
        return self.profile in manifest.supported_profiles

    def _application_tags_match(self, manifest: ToolManifest) -> bool:
        if self.application_tags is None:
            return True
        tags = manifest.metadata.get("application_tags")
        if not isinstance(tags, list):
            return False
        normalized = {str(item) for item in tags}
        return any(tag in normalized for tag in self.application_tags)

    def apply(self, source: ToolRegistry) -> ToolRegistry:
        """Build a filtered registry from source tools."""
        filtered = ToolRegistry()
        for name in source.list_names():
            registered = source.get(name)
            if registered is None:
                continue
            if self._matches(registered.manifest):
                filtered.register(registered.manifest, registered.handler)
        return filtered

    def unknown_names(self, source: ToolRegistry) -> tuple[str, ...]:
        """Return explicit names requested but absent in source registry."""
        if self.names is None:
            return ()
        known = set(source.list_names())
        missing = sorted(name for name in self.names if name not in known)
        return tuple(missing)

    def validate_known_names(self, source: ToolRegistry) -> None:
        """Fail fast when ToolSet.only contains unknown names."""
        missing = self.unknown_names(source)
        if not missing:
            return
        raise ValueError(f"unknown tool names in ToolSet: {', '.join(missing)}")

    def manifests(self, source: ToolRegistry) -> list[ToolManifest]:
        """Return selected manifests for prompt/doc surface rendering."""
        return [item.manifest for item in self._iter_selected(source)]

    def _iter_selected(self, source: ToolRegistry):
        for name in source.list_names():
            registered = source.get(name)
            if registered is None:
                continue
            if self._matches(registered.manifest):
                yield registered


def builtin_pack_names() -> tuple[str, ...]:
    """Return stable built-in pack names."""
    return tuple(sorted(_BUILTIN_PACKS))


__all__ = ["ToolSet", "builtin_pack_names"]
