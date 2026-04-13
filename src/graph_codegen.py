"""
graph_codegen.py -- LangGraph StateGraph for HTAF code generation pipeline.

Linear graph: parse_tse -> map_requirements -> generate_tests -> validate_code -> export_tests

This is a separate subgraph from the main verification graph (graph.py).
Both share the same AgentState type.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .nodes.export_tests import export_tests
from .nodes.generate_tests import generate_tests
from .nodes.map_requirements import map_requirements
from .nodes.parse_tse import parse_tse
from .nodes.validate_code import validate_code
from .state import AgentState


def build_codegen_graph() -> StateGraph:
    """Build the HTAF code generation pipeline graph."""
    graph = StateGraph(AgentState)

    graph.add_node("parse_tse", parse_tse)
    graph.add_node("map_requirements", map_requirements)
    graph.add_node("generate_tests", generate_tests)
    graph.add_node("validate_code", validate_code)
    graph.add_node("export_tests", export_tests)

    graph.set_entry_point("parse_tse")
    graph.add_edge("parse_tse", "map_requirements")
    graph.add_edge("map_requirements", "generate_tests")
    graph.add_edge("generate_tests", "validate_code")
    graph.add_edge("validate_code", "export_tests")
    graph.add_edge("export_tests", END)

    return graph


def compile_codegen_graph():
    """Build and compile the codegen graph, ready to invoke."""
    return build_codegen_graph().compile()
