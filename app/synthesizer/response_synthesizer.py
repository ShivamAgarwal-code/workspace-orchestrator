"""Aggregates the orchestrator's ExecutionReport into a natural-language response.

Builds a compact, structured JSON context (results per service, errors per failed node, actions
taken, any pending confirmation or clarification question) and hands it to the LLM as the single
source of truth — the LLM's job is presentation, not fact invention, which keeps the response
grounded in what was actually fetched/executed.
"""
import json

from app.intent.schemas import Intent
from app.llm.base import ChatMessage, LLMProvider
from app.orchestrator.reporting import split_results_and_errors
from app.orchestrator.types import ExecutionReport, NodeStatus
from app.planner.dag import ExecutionDAG
from app.utils.serialization import json_safe

SYNTHESIZER_SYSTEM_PROMPT = """You are the response-synthesis component of a Google Workspace \
orchestrator. You receive a JSON context describing what was searched/executed across Gmail, \
Calendar, and Drive in response to a user's query, and must produce a single, coherent, concise \
natural-language reply.

Rules:
- Only state facts present in the JSON context. Never invent emails, events, files, or dates.
- If `clarification_question` is present, ask exactly that question and do not take further \
  action or make assumptions about which item the user meant.
- If `errors` is non-empty, briefly and gracefully note which service could not be reached, but \
  still present whatever results ARE available — a partial answer beats no answer.
- If `pending_confirmation` is present (e.g. a drafted-but-not-sent email), end by asking the \
  user to confirm before anything is actually sent/deleted/modified further.
- Use short paragraphs / bullet points. No filler like "I'd be happy to help".
"""


class ResponseSynthesizer:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def synthesize(self, query: str, intent: Intent, dag: ExecutionDAG, report: ExecutionReport) -> str:
        context = self._build_context(query, intent, dag, report)
        user_message = f"Context:\n```json\n{json.dumps(context, indent=2)}\n```"
        return await self._llm.complete(
            system=SYNTHESIZER_SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=user_message)],
            max_tokens=800,
        )

    def _build_context(self, query: str, intent: Intent, dag: ExecutionDAG, report: ExecutionReport) -> dict:
        results, errors = split_results_and_errors(dag, report)

        pending_confirmation = None
        for node_id, result in report.results.items():
            node = dag.nodes.get(node_id)
            if node and node.action == "draft_email" and result.status == NodeStatus.ok:
                pending_confirmation = "send this email"

        conflict_data = None
        for node_id, result in report.results.items():
            node = dag.nodes.get(node_id)
            if node and node.id == "detect_conflicts" and result.status == NodeStatus.ok:
                conflict_data = result.data

        return json_safe({
            "user_query": query,
            "intent": intent.intent,
            "results": results,
            "errors": errors,
            "actions_taken": report.actions_taken,
            "pending_confirmation": pending_confirmation,
            "conflicts": conflict_data,
            "needs_clarification": intent.needs_clarification,
            "clarification_question": intent.clarification_question,
        })
