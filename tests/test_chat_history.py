"""The chat route records every turn in the history and replays the stored transcript to an agent that lost it."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.agent.base import AgentBackend, done, error, status, text_delta, ui_component
from src.chat_history import ChatHistoryStore, with_transcript


class FakeApi:
    """Just the two chat-history endpoints, in memory."""

    def __init__(self):
        self.messages: list[dict] = []  # (headers, session, body)
        self.transcripts: dict[str, list[dict]] = {}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        parts = request.url.path.strip("/").split("/")
        assert parts[:2] == ["api", "chat-sessions"]
        sid = parts[2]
        if request.method == "POST" and parts[3] == "messages":
            self.messages.append({"session": sid, "user": request.headers.get("x-user-id"),
                                  "workspace": request.headers.get("x-workspace-id"), **json.loads(request.content)})
            return httpx.Response(201, json={"session": {"id": sid}, "message": {}})
        if request.method == "GET" and parts[3] == "transcript":
            return httpx.Response(200, json={"messages": self.transcripts.get(sid, [])})
        return httpx.Response(404)

    def store(self) -> ChatHistoryStore:
        s = ChatHistoryStore("http://api.test")
        s._client = httpx.AsyncClient(base_url="http://api.test", transport=httpx.MockTransport(self.handler))
        return s


class Recorder(AgentBackend):
    name = "stub"

    def __init__(self):
        self.seen: list[str] = []
        self.known: set[str] = set()

    def has_session(self, session_id, user_id, workspace_id=None):
        return session_id in self.known

    async def run_turn(self, session_id, user_id, message, workspace_id=None):
        self.seen.append(message)
        self.known.add(session_id)
        if "fail" in message:
            yield error("kaputt")
            return
        yield status("spending_report")
        yield text_delta("Hai speso ")
        yield text_delta("300 €.")
        yield ui_component({"type": "SpendingChart", "order": 0})
        yield done("Hai speso 300 €.", [{"type": "SpendingChart", "order": 0}])


@pytest.fixture
def chat(monkeypatch):
    api, backend = FakeApi(), Recorder()
    monkeypatch.setenv("FINANCE_CHAT_HISTORY", "on")  # conftest switches it off for every other test
    monkeypatch.setattr("src.routes.agent_chat.get_backend", lambda: backend)
    monkeypatch.setattr("src.routes.agent_chat.get_history", api.store)
    from app import app

    return TestClient(app), api, backend


def events(res):
    return [json.loads(chunk[len("data: "):]) for chunk in res.text.strip().split("\n\n")]


def test_a_turn_records_the_question_and_the_answer_with_its_components_and_tools(chat):
    client, api, backend = chat
    res = client.post("/chat/stream", json={"message": "quanto ho speso?", "session_id": "s1", "user_id": "u1", "workspace_id": 3})
    assert events(res)[-1]["type"] == "done"
    assert [m["role"] for m in api.messages] == ["user", "assistant"]
    user, assistant = api.messages
    assert user == {"session": "s1", "user": "u1", "workspace": "3", "role": "user", "content": "quanto ho speso?", "backend": "stub"}
    assert assistant["content"] == "Hai speso 300 €."
    assert assistant["components"] == [{"type": "SpendingChart", "order": 0}]
    assert assistant["tools"] == ["spending_report"]
    assert "error" not in assistant
    assert backend.seen == ["quanto ho speso?"]  # nothing stored yet: no transcript prefix


def test_an_error_turn_is_kept_with_the_error(chat):
    client, api, _ = chat
    client.post("/chat/stream", json={"message": "fail please", "session_id": "s1", "user_id": "u1"})
    assert api.messages[-1]["role"] == "assistant" and api.messages[-1]["error"] == "kaputt"
    assert api.messages[-1]["content"] == ""
    assert api.messages[0]["workspace"] is None  # default project: no header


def test_the_stored_transcript_is_replayed_only_when_the_engine_lost_the_session(chat):
    client, api, backend = chat
    api.transcripts["s9"] = [{"role": "user", "content": "ciao"}, {"role": "assistant", "content": "Ciao! Come posso aiutarti?"}]
    client.post("/chat/stream", json={"message": "e marzo?", "session_id": "s9", "user_id": "u1"})
    first = backend.seen[0]
    assert first.startswith("[Earlier in this conversation")
    assert "user: ciao\nassistant: Ciao! Come posso aiutarti?" in first
    assert first.endswith("\n\ne marzo?")
    assert api.messages[0]["content"] == "e marzo?"  # the history keeps the user's words, not the prefix

    client.post("/chat/stream", json={"message": "e aprile?", "session_id": "s9", "user_id": "u1"})
    assert backend.seen[1] == "e aprile?"  # the engine holds the session now: no replay


def test_an_unreachable_api_never_breaks_the_chat(chat, caplog):
    client, api, backend = chat
    api.down = True
    res = client.post("/chat/stream", json={"message": "ciao", "session_id": "s1", "user_id": "u1"})
    assert events(res)[-1]["type"] == "done"
    assert backend.seen == ["ciao"]
    assert any("Chat history not recorded" in r.message for r in caplog.records)


def test_history_can_be_switched_off(chat, monkeypatch):
    client, api, _ = chat
    monkeypatch.setenv("FINANCE_CHAT_HISTORY", "off")
    client.post("/chat/stream", json={"message": "ciao", "session_id": "s1", "user_id": "u1"})
    assert api.messages == []


def test_with_transcript_is_the_identity_without_history():
    assert with_transcript([], "ciao") == "ciao"
