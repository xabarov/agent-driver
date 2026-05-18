"""Minimal prompt template registry and renderer."""

from __future__ import annotations

from hashlib import sha256

from agent_driver.contracts.enums import AgentProfile
from agent_driver.contracts.profiles import PromptRenderResult, PromptTemplate


class PromptTemplateRegistry:
    """In-memory prompt template registry keyed by id/profile/version."""

    def __init__(self) -> None:
        self._templates: dict[tuple[str, AgentProfile, int], PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        """Register template by `(template_id, profile, version)`."""
        key = (template.template_id, template.profile, template.version)
        self._templates[key] = template

    def get(
        self, *, template_id: str, profile: AgentProfile, version: int
    ) -> PromptTemplate | None:
        """Return one template by explicit key."""
        return self._templates.get((template_id, profile, version))

    def render(
        self,
        *,
        template_id: str,
        profile: AgentProfile,
        version: int,
        values: dict[str, str],
    ) -> PromptRenderResult:
        """Render one template with required-placeholder checks."""
        template = self.get(template_id=template_id, profile=profile, version=version)
        if template is None:
            raise ValueError(
                "prompt template not found: "
                f"id={template_id} profile={profile.value} version={version}"
            )
        missing = [
            placeholder
            for placeholder in template.required_placeholders
            if placeholder not in values
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"missing required placeholders: {joined}")
        rendered = template.body
        for key, value in values.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        rendered_hash = sha256(rendered.encode("utf-8")).hexdigest()
        return PromptRenderResult(
            template_id=template.template_id,
            template_version=template.version,
            profile=template.profile,
            rendered_text=rendered,
            rendered_hash=rendered_hash,
        )
