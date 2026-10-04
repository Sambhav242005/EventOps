"""M1 tests: mock LLM routing + graceful errors. No network."""
import asyncio
import os
os.environ["LLM_BACKEND"] = "mock"

from app.llm import MockLLMAdapter
from app.models import AgentAction


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_mock_routes_call():
    a = run(MockLLMAdapter().complete_action("", "@agent call the caterer"))
    assert isinstance(a, AgentAction) and a.type == "call_vendor"


def test_mock_routes_research():
    a = run(MockLLMAdapter().complete_action("", "find backup caterers"))
    assert a.type == "research"


def test_mock_routes_draft():
    a = run(MockLLMAdapter().complete_action("", "draft a message to the team"))
    assert a.type == "draft_message"


def test_mock_routes_export():
    a = run(MockLLMAdapter().complete_action("", "export csv please"))
    assert a.type == "export_csv"


def test_mock_answer_and_clarify():
    a = run(MockLLMAdapter().complete_action("", "hello there team"))
    assert a.type == "answer"
    b = run(MockLLMAdapter().complete_action("", "help?"))
    assert b.type == "ask_clarification"
