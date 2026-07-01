"""Defer primer — retrieval-primed surfacing of deferred tools.

Deferred tools (``manifest.should_defer``) are omitted from the schema list and
normally surface only when the model calls ``tool_search``. A weaker model that
never calls it silently loses the capability. The defer primer closes that gap:
before each step it scores deferred tools against the conversation and surfaces
the relevant ones directly (no model cooperation needed). ``tool_search`` stays
as a backstop. Default (no primer) leaves the schema list unchanged.
"""
from agent_driver.contracts import ToolManifest
from agent_driver.runtime.single_agent.llm_step.build import (
    _request_tools_from_registry,
)
from agent_driver.runtime.single_agent.llm_step.defer_primer import (
    DeferPrimerInput,
    keyword_relevance_primer,
    surfaced_deferred_tool_names,
)
from agent_driver.tools import ToolRegistry


async def _noop(_args):
    return {}


_OBJ_SCHEMA = {"type": "object", "properties": {}}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolManifest(name="read_x", description="read a value", args_schema=_OBJ_SCHEMA), _noop)
    reg.register(
        ToolManifest(
            name="chart_vegalite",
            description="Render a Vega-Lite chart from rows",
            args_schema=_OBJ_SCHEMA,
            should_defer=True,
        ),
        _noop,
    )
    reg.register(
        ToolManifest(
            name="render_markdown_table",
            description="Format rows as a markdown table",
            args_schema=_OBJ_SCHEMA,
            should_defer=True,
        ),
        _noop,
    )
    return reg


def _schema_names(schemas):
    return {s["function"]["name"] for s in schemas}


def _deferred(reg):
    return tuple(item.manifest for item in reg.list_registered() if item.manifest.is_deferred())


# --- keyword_relevance_primer ------------------------------------------------


def test_primer_surfaces_on_exact_name_mention():
    primer = keyword_relevance_primer()
    out = primer(
        DeferPrimerInput(
            conversation_text="please build it with chart_vegalite now",
            deferred=_deferred(_registry()),
        )
    )
    assert out == ["chart_vegalite"]


def test_primer_surfaces_on_token_overlap():
    primer = keyword_relevance_primer()
    out = list(
        primer(
            DeferPrimerInput(
                conversation_text="draw a vega-lite chart of the totals",
                deferred=_deferred(_registry()),
            )
        )
    )
    assert out[0] == "chart_vegalite"
    assert "render_markdown_table" not in out  # no overlap with this text


def test_primer_returns_empty_when_no_signal():
    primer = keyword_relevance_primer()
    out = primer(
        DeferPrimerInput(
            conversation_text="compute the sum of column b",
            deferred=_deferred(_registry()),
        )
    )
    assert out == []


def test_primer_respects_max_tools_and_name_mention_wins():
    primer = keyword_relevance_primer(max_tools=1)
    # Both relevant; the exact-name mention must outrank the overlap-only hit.
    out = primer(
        DeferPrimerInput(
            conversation_text="render_markdown_table please, and a vega-lite chart too",
            deferred=_deferred(_registry()),
        )
    )
    assert out == ["render_markdown_table"]


def test_primer_empty_conversation_returns_empty():
    primer = keyword_relevance_primer()
    assert primer(DeferPrimerInput(conversation_text="   ", deferred=_deferred(_registry()))) == []


# --- surfaced_deferred_tool_names (the glue helper) --------------------------


def test_surfaced_names_none_primer_is_noop():
    assert surfaced_deferred_tool_names(_deferred(_registry()), "chart_vegalite", None) == ()


def test_surfaced_names_drops_unknown_and_dedupes():
    def primer(_payload):
        return ["chart_vegalite", "chart_vegalite", "not_a_real_tool"]

    out = surfaced_deferred_tool_names(_deferred(_registry()), "anything", primer)
    assert out == ("chart_vegalite",)


def test_surfaced_names_empty_when_nothing_deferred():
    assert surfaced_deferred_tool_names((), "chart_vegalite", keyword_relevance_primer()) == ()


# --- integration with the schema builder -------------------------------------


def test_surfaced_deferred_tool_enters_schema_list():
    names = _schema_names(
        _request_tools_from_registry(
            _registry(), allowed=None, denied=None, surface_deferred=("chart_vegalite",)
        )
    )
    assert "chart_vegalite" in names  # surfaced by the primer
    assert "render_markdown_table" not in names  # still deferred
    assert "read_x" in names


def test_surfaced_deferred_tool_still_gated_by_deny():
    # A surfaced name that's denied must NOT leak into the schema list.
    names = _schema_names(
        _request_tools_from_registry(
            _registry(),
            allowed=None,
            denied=("chart_vegalite",),
            surface_deferred=("chart_vegalite",),
        )
    )
    assert "chart_vegalite" not in names


def test_no_surface_keeps_default_deferral():
    names = _schema_names(_request_tools_from_registry(_registry(), allowed=None, denied=None))
    assert "chart_vegalite" not in names
    assert "render_markdown_table" not in names
    assert "read_x" in names
