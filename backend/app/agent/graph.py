from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.answer_composer_node import answer_composer_node
from app.agent.nodes.chart_planner import chart_planner_node
from app.agent.nodes.clarification_checker import clarification_checker
from app.agent.nodes.clarification_reply import clarification_reply
from app.agent.nodes.complexity_router import complexity_router
from app.agent.nodes.intent_analyzer import intent_analyzer
from app.agent.nodes.memory_load import memory_load
from app.agent.nodes.memory_save import memory_save
from app.agent.nodes.react_agent import react_agent
from app.agent.nodes.react_tools import react_tools_node
from app.agent.nodes.schema_retriever import schema_retriever
from app.agent.nodes.slot_merge import slot_merge
from app.agent.nodes.sql_executor_node import sql_executor_node
from app.agent.nodes.sql_generator_node import sql_generator_node
from app.agent.nodes.sql_guardrail_node import sql_guardrail_node
from app.agent.nodes.sql_repairer import sql_repairer
from app.agent.react_subgraph import after_react_agent, after_react_tools
from app.agent.state import AgentState


def _after_memory_load(state: AgentState) -> str:
    if state.get("error"):
        return "MemorySave"
    return "IntentAnalyzer"


def _after_router(state: AgentState) -> str:
    if state.get("need_clarification"):
        return "ClarificationReply"
    if state.get("route_mode") == "react":
        return "ReActAgent"
    return "SchemaRetriever"


def _after_guardrail(state: AgentState) -> str:
    if state.get("error"):
        return "MemorySave"
    return "SQLExecutor"


def _after_executor(state: AgentState) -> str:
    if not state.get("error"):
        return "ChartPlanner"
    if state.get("repaired"):
        return "MemorySave"
    return "SQLRepairer"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("MemoryLoad", memory_load)
    g.add_node("IntentAnalyzer", intent_analyzer)
    g.add_node("SlotMerge", slot_merge)
    g.add_node("ClarificationChecker", clarification_checker)
    g.add_node("ComplexityRouter", complexity_router)
    g.add_node("ClarificationReply", clarification_reply)
    g.add_node("ReActAgent", react_agent)
    g.add_node("ReActTools", react_tools_node)
    g.add_node("SchemaRetriever", schema_retriever)
    g.add_node("SQLGenerator", sql_generator_node)
    g.add_node("SQLGuardrail", sql_guardrail_node)
    g.add_node("SQLExecutor", sql_executor_node)
    g.add_node("SQLRepairer", sql_repairer)
    g.add_node("ChartPlanner", chart_planner_node)
    g.add_node("AnswerComposer", answer_composer_node)
    g.add_node("MemorySave", memory_save)
    g.add_edge(START, "MemoryLoad")
    g.add_conditional_edges(
        "MemoryLoad",
        _after_memory_load,
        {
            "IntentAnalyzer": "IntentAnalyzer",
            "MemorySave": "MemorySave",
        },
    )
    g.add_edge("IntentAnalyzer", "SlotMerge")
    g.add_edge("SlotMerge", "ClarificationChecker")
    g.add_edge("ClarificationChecker", "ComplexityRouter")
    g.add_conditional_edges(
        "ComplexityRouter",
        _after_router,
        {
            "ClarificationReply": "ClarificationReply",
            "ReActAgent": "ReActAgent",
            "SchemaRetriever": "SchemaRetriever",
        },
    )
    g.add_edge("ClarificationReply", "MemorySave")
    g.add_conditional_edges(
        "ReActAgent",
        after_react_agent,
        {
            "ReActTools": "ReActTools",
            "SQLGuardrail": "SQLGuardrail",
            "MemorySave": "MemorySave",
        },
    )
    g.add_conditional_edges(
        "ReActTools",
        after_react_tools,
        {
            "ReActAgent": "ReActAgent",
            "SQLGuardrail": "SQLGuardrail",
            "MemorySave": "MemorySave",
        },
    )
    g.add_edge("SchemaRetriever", "SQLGenerator")
    g.add_edge("SQLGenerator", "SQLGuardrail")
    g.add_conditional_edges(
        "SQLGuardrail",
        _after_guardrail,
        {
            "SQLExecutor": "SQLExecutor",
            "MemorySave": "MemorySave",
        },
    )
    g.add_conditional_edges(
        "SQLExecutor",
        _after_executor,
        {
            "ChartPlanner": "ChartPlanner",
            "SQLRepairer": "SQLRepairer",
            "MemorySave": "MemorySave",
        },
    )
    g.add_edge("SQLRepairer", "SQLGuardrail")
    g.add_edge("ChartPlanner", "AnswerComposer")
    g.add_edge("AnswerComposer", "MemorySave")
    g.add_edge("MemorySave", END)
    return g.compile()
