"""
agent/graph.py — LangGraph Agent Graph Definition

Defines the full multi-node reasoning graph:

  planner → repo_analyzer → bug_reproducer → fix_generator
                                                     ↓
                              pr_creator ← test_runner
                                  ↑              ↓
                                  └── reflector ─┘ (retry loop)
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from agent.state import AgentState
from agent.nodes.planner import planner_node
from agent.nodes.repo_analyzer import repo_analyzer_node
from agent.nodes.bug_reproducer import bug_reproducer_node
from agent.nodes.fix_generator import fix_generator_node
from agent.nodes.test_runner import test_runner_node
from agent.nodes.reflector import reflector_node
from agent.nodes.pr_creator import pr_creator_node

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conditional edge functions — routing logic
# ---------------------------------------------------------------------------

def route_after_planner(state: AgentState) -> str:
    """If planner errored, go to END; otherwise proceed."""
    if state.get("error"):
        logger.warning(f"[Router] Planner failed: {state['error']}")
        return "end"
    return "repo_analyzer"


def route_after_reproducer(state: AgentState) -> str:
    """If reproduction failed critically, stop; otherwise generate fix."""
    if state.get("error"):
        return "end"
    return "fix_generator"


def route_after_fix_generator(state: AgentState) -> str:
    """If fix generation failed, stop; otherwise run tests."""
    if state.get("error"):
        return "end"
    return "test_runner"


def route_after_test_runner(state: AgentState) -> str:
    """
    Core routing logic:
    - If tests pass → create PR
    - If tests fail → reflect (may retry or stop)
    - If critical error → stop
    """
    if state.get("error"):
        return "end"
    if state.get("validation_success"):
        return "pr_creator"
    return "reflector"


def route_after_reflector(state: AgentState) -> str:
    """
    Retry loop gate:
    - should_retry=True  → go back to fix_generator
    - should_retry=False → we've hit max iterations, stop
    """
    if state.get("should_retry"):
        return "fix_generator"
    return "end"


def route_after_pr_creator(state: AgentState) -> str:
    """Always terminate after PR creation (success or failure)."""
    return "end"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """Construct and compile the LangGraph agent graph."""
    
    graph = StateGraph(AgentState)

    # -------------------------------------------------------------------------
    # Register nodes
    # -------------------------------------------------------------------------
    graph.add_node("planner",        planner_node)
    graph.add_node("repo_analyzer",  repo_analyzer_node)
    graph.add_node("bug_reproducer", bug_reproducer_node)
    graph.add_node("fix_generator",  fix_generator_node)
    graph.add_node("test_runner",    test_runner_node)
    graph.add_node("reflector",      reflector_node)
    graph.add_node("pr_creator",     pr_creator_node)

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------
    graph.set_entry_point("planner")

    # -------------------------------------------------------------------------
    # Edges (deterministic)
    # -------------------------------------------------------------------------
    graph.add_edge("repo_analyzer",  "bug_reproducer")

    # -------------------------------------------------------------------------
    # Conditional edges (routing logic)
    # -------------------------------------------------------------------------
    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {"repo_analyzer": "repo_analyzer", "end": END},
    )
    graph.add_conditional_edges(
        "bug_reproducer",
        route_after_reproducer,
        {"fix_generator": "fix_generator", "end": END},
    )
    graph.add_conditional_edges(
        "fix_generator",
        route_after_fix_generator,
        {"test_runner": "test_runner", "end": END},
    )
    graph.add_conditional_edges(
        "test_runner",
        route_after_test_runner,
        {"pr_creator": "pr_creator", "reflector": "reflector", "end": END},
    )
    graph.add_conditional_edges(
        "reflector",
        route_after_reflector,
        {"fix_generator": "fix_generator", "end": END},
    )
    graph.add_conditional_edges(
        "pr_creator",
        route_after_pr_creator,
        {"end": END},
    )

    return graph.compile()


# ---------------------------------------------------------------------------
# Singleton — compiled graph reused across tasks
# ---------------------------------------------------------------------------

agent_graph = build_graph()


# ---------------------------------------------------------------------------
# Run helper for use by the worker
# ---------------------------------------------------------------------------

async def run_agent(initial_state: AgentState) -> AgentState:
    """
    Execute the full agent graph with the given initial state.
    
    Returns the final state after the graph terminates.
    Cleanups (container removal) are handled by the worker.
    """
    logger.info(f"[Graph] Starting agent for task {initial_state['task_id']}")
    
    final_state = await agent_graph.ainvoke(initial_state)
    
    if final_state.get("pr_url"):
        logger.info(f"[Graph] ✅ Completed. PR: {final_state['pr_url']}")
    elif final_state.get("error"):
        logger.error(f"[Graph] ❌ Failed: {final_state['error']}")
    else:
        logger.warning("[Graph] ⚠️ Terminated without PR or error.")
    
    return final_state
