"""LangGraph workflow graph definition."""

from langgraph.graph import StateGraph, END
from src.workflow.state import FinanceState
from src.workflow.nodes import (
    asr_node,
    context_node,
    nlu_node,
    onboarding_node,
    query_node,
    route_after_asr,
    ui_planner_node,
    generator_node,
)



def create_assistant_graph():
    """Create the finance assistant workflow graph.

    Returns:
        Compiled LangGraph workflow
    """
    workflow = StateGraph(FinanceState)
    workflow.add_node("context", context_node)
    workflow.add_node("asr", asr_node)
    workflow.add_node("onboarding", onboarding_node)
    workflow.add_node("nlu", nlu_node)
    workflow.add_node("query", query_node)
    workflow.add_node("ui_planner", ui_planner_node)
    workflow.add_node("generator", generator_node)

    workflow.set_entry_point("context")
    workflow.add_edge("context", "asr")
    workflow.add_conditional_edges(
        "asr", route_after_asr, {"onboarding": "onboarding", "normal": "nlu"}
    )
    workflow.add_edge("onboarding", "ui_planner")
    workflow.add_edge("nlu", "query")
    workflow.add_edge("query", "ui_planner")
    workflow.add_edge("ui_planner", "generator")
    workflow.add_edge("generator", END)
    return workflow.compile()
