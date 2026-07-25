from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.answer_composer_node import answer_composer_node
from app.agent.nodes.clarification_checker import clarification_checker
from app.agent.nodes.clarification_reply import clarification_reply
from app.agent.nodes.intent_analyzer import intent_analyzer
from app.agent.nodes.route_emit import route_emit
from app.agent.nodes.schema_retriever import schema_retriever
from app.agent.nodes.sql_executor_node import sql_executor_node
from app.agent.nodes.sql_generator_node import sql_generator_node
from app.agent.nodes.sql_guardrail_node import sql_guardrail_node
from app.agent.state import AgentState


def _after_route(state: AgentState) -> str:
    if state.get("need_clarification"):
        return "ClarificationReply"
    return "SchemaRetriever"


def _after_guardrail(state: AgentState) -> str:
    if state.get("error"):
        return END
    return "SQLExecutor"


def _after_executor(state: AgentState) -> str:
    if state.get("error"):
        return END
    return "AnswerComposer"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("IntentAnalyzer", intent_analyzer)
    g.add_node("ClarificationChecker", clarification_checker)
    g.add_node("RouteEmit", route_emit)
    g.add_node("ClarificationReply", clarification_reply)
    g.add_node("SchemaRetriever", schema_retriever)
    g.add_node("SQLGenerator", sql_generator_node)
    g.add_node("SQLGuardrail", sql_guardrail_node)
    g.add_node("SQLExecutor", sql_executor_node)
    g.add_node("AnswerComposer", answer_composer_node)
    g.add_edge(START, "IntentAnalyzer")
    g.add_edge("IntentAnalyzer", "ClarificationChecker")
    g.add_edge("ClarificationChecker", "RouteEmit")
    g.add_conditional_edges(
        "RouteEmit",
        _after_route,
        {
            "ClarificationReply": "ClarificationReply",
            "SchemaRetriever": "SchemaRetriever",
        },
    )
    g.add_edge("ClarificationReply", END)
    g.add_edge("SchemaRetriever", "SQLGenerator")
    g.add_edge("SQLGenerator", "SQLGuardrail")
    g.add_conditional_edges(
        "SQLGuardrail",
        _after_guardrail,
        {
            "SQLExecutor": "SQLExecutor",
            END: END,
        },
    )
    g.add_conditional_edges(
        "SQLExecutor",
        _after_executor,
        {
            "AnswerComposer": "AnswerComposer",
            END: END,
        },
    )
    g.add_edge("AnswerComposer", END)
    return g.compile()
