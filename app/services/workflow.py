import uuid
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from app.services.graph_retriever import retriever_service
from app.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# STATE DEFINITION
# ============================================================

class KYCAgentState(TypedDict):
    # Investigation control
    question:            str
    investigation_depth: str          # "basic" | "deep"

    # Graph retrieval pipeline
    cypher_query:        Optional[str]
    graph_context:       Optional[str]
    answer:              Optional[str]
    query_decomposition: Optional[str]
    query_advice:        Optional[str]


# ============================================================
# GRAPH BUILDER
# ============================================================

def build_kyc_graph():
    """
    KYC LangGraph:

      planning
        └── write_query
              └── run_query
                    └── answer_user ──► END
    """
    workflow = StateGraph(KYCAgentState)

    workflow.add_node("planning",    retriever_service.query_decomposition)
    workflow.add_node("write_query", retriever_service.generate_cypher)
    workflow.add_node("run_query",   retriever_service.execute_query)
    workflow.add_node("answer_user", retriever_service.generate_answer)

    workflow.set_entry_point("planning")

    workflow.add_edge("planning",    "write_query")
    workflow.add_edge("write_query", "run_query")
    workflow.add_edge("run_query",   "answer_user")
    workflow.add_edge("answer_user", END)

    return workflow.compile()


kyc_agent = build_kyc_graph()


def build_retriever_graph():
    return build_kyc_graph()
