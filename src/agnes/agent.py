"""
Agnes -- AI CPG Procurement Intelligence Agent.
Powered by Claude with tool use for CPG ingredient sourcing,
substitution analysis, supplier discovery, compliance, and consolidation.
"""

import json
import os
from typing import Any, Optional, Iterator

from .prompt import AGNES_SYSTEM_PROMPT, AGNES_TOOLS
from .tools import execute_tool


class Agnes:
    """
    Agnes AI CPG Procurement Intelligence Agent.
    Manages conversation state and tool execution against the CPG database.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        db_path: str = "db.sqlite",
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.db_path = db_path
        self.conversation_history: list[dict] = []

        # Lazy-loaded
        self._cpg_db = None
        self._pipeline = None

    @property
    def cpg_db(self):
        if self._cpg_db is None:
            from src.procurement.cpg_db import CpgDatabase
            self._cpg_db = CpgDatabase(self.db_path)
        return self._cpg_db

    @property
    def pipeline(self):
        if self._pipeline is None:
            from .pipeline import AgnesPipeline
            self._pipeline = AgnesPipeline(self.cpg_db)
        return self._pipeline

    def chat(self, user_message: str) -> dict:
        """
        Send a message to Agnes and get a response.
        Handles multi-turn conversation and tool use automatically.

        Returns dict with:
          - response: Agnes's text response
          - tool_calls: list of tools called
          - usage: token usage
        """
        if not self.api_key:
            return {
                "response": "Error: ANTHROPIC_API_KEY not set. Please configure your API key.",
                "tool_calls": [],
                "usage": {},
            }

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            return {
                "response": "Error: anthropic package not installed. Run: pip install anthropic",
                "tool_calls": [],
                "usage": {},
            }

        self.conversation_history.append({
            "role": "user",
            "content": user_message,
        })

        tool_calls_made: list[dict] = []
        total_input_tokens = 0
        total_output_tokens = 0

        max_iterations = 10
        for _ in range(max_iterations):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=AGNES_SYSTEM_PROMPT,
                tools=AGNES_TOOLS,
                messages=self.conversation_history,
            )

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            if response.stop_reason == "end_turn":
                text_response = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text_response += block.text

                self.conversation_history.append({
                    "role": "assistant",
                    "content": response.content,
                })

                return {
                    "response": text_response,
                    "tool_calls": tool_calls_made,
                    "usage": {
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                    },
                }

            elif response.stop_reason == "tool_use":
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        result = execute_tool(
                            block.name,
                            block.input,
                            self.cpg_db,
                            self.pipeline,
                        )

                        tool_calls_made.append({
                            "tool": block.name,
                            "input": block.input,
                            "result_summary": _summarize_result(result),
                        })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })

                self.conversation_history.append({
                    "role": "assistant",
                    "content": response.content,
                })
                self.conversation_history.append({
                    "role": "user",
                    "content": tool_results,
                })
            else:
                break

        return {
            "response": "Agnes reached maximum tool call iterations. Please try a more specific question.",
            "tool_calls": tool_calls_made,
            "usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
        }

    def reset_conversation(self):
        """Clear conversation history for a new session."""
        self.conversation_history = []

    def get_conversation_summary(self) -> str:
        """Get a summary of the current conversation."""
        turns = len([m for m in self.conversation_history if m.get("role") == "user"])
        return f"Conversation has {turns} user turns, {len(self.conversation_history)} total messages."


def _summarize_result(result: Any) -> str:
    """Create a brief summary of a tool result for logging."""
    if isinstance(result, dict):
        if "error" in result:
            return f"ERROR: {result['error']}"
        if "candidates" in result:
            count = len(result.get("candidates", []))
            return f"Found {count} candidates"
        if "found" in result:
            return f"found={result['found']}"
        if "ingredients" in result:
            return f"{len(result.get('ingredients', []))} ingredients"
        if "count" in result:
            return f"{result['count']} results"
        if "status" in result:
            return f"status={result['status']}"
        if "run_id" in result:
            return f"pipeline run {result['run_id']}"
    return str(result)[:100]
