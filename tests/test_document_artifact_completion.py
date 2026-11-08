"""Typed completion proof for native Documents named like text files."""
from dataclasses import replace
import hashlib

import pytest

from src.agent_evidence import (
    CompletionRequirements, CompletionStatus, EvidenceKind, EvidenceLedger,
    infer_completion_requirements, requirements_from_runtime_context,
)
from src.agent_runtime.completion import completion_answer


def document_event(title="test.txt", **changes):
    return {"tool": "create_document", "command": title + "\ntext\nRandom text.",
            "exit_code": 0, "execution_attempted": True,
            "action_id": "run:action:1", "execution_id": "execution-1",
            "document_result": {"id":"native-doc-id","title":title,"nonempty":True}, **changes}


@pytest.mark.parametrize("prompt", [
    "make a doicument test.txt wiht some random tet in it",
    "Make a document test.txt with random text",
    "Create a document titled test.txt",
    "Create a document test.txt in the document library",
])
def test_native_document_satisfies_its_named_output_without_a_disk_file(tmp_path, prompt):
    requirements = replace(infer_completion_requirements(prompt), workspace_root=str(tmp_path))
    assert requirements.document_alternatives == ("test.txt",)
    ledger = EvidenceLedger.from_tool_events([document_event()], requirements)
    decision = ledger.evaluate()
    assert decision.status == CompletionStatus.SATISFIED and decision.can_complete
    assert not (tmp_path / "test.txt").exists()
    assert ledger._supports_artifact_claim(EvidenceKind.ARTIFACT_MUTATION, ("test.txt",))


@pytest.mark.parametrize("prompt", [
    "Create a file test.txt",
    "Make a document test.txt on disk",
    "Create /workspace/test.txt",
    "Create a document test.txt in the workspace",
])
def test_document_receipt_cannot_satisfy_a_filesystem_obligation(tmp_path, prompt):
    requirements = replace(infer_completion_requirements(prompt), workspace_root=str(tmp_path))
    assert requirements.document_alternatives == ()
    ledger = EvidenceLedger.from_tool_events([document_event()], requirements)
    assert not ledger.evaluate().can_complete


@pytest.mark.parametrize("changes", [
    {"exit_code": None}, {"exit_code": 1, "error": True}, {"blocked": True},
    {"execution_attempted": False}, {"action_id": ""}, {"execution_id": ""},
    {"document_result": {"id":"another","title":"other.txt","nonempty":True}},
    {"document_result": {"id":"native-doc-id","title":"test.txt","nonempty":False}},
    {"document_result": None},
])
def test_failed_unbound_or_wrong_document_is_not_output_evidence(changes):
    requirements = infer_completion_requirements("Make a document test.txt")
    ledger = EvidenceLedger.from_tool_events([document_event(**changes)], requirements)
    assert not ledger.evaluate().can_complete


def test_document_wording_still_accepts_a_real_workspace_file(tmp_path):
    (tmp_path / "test.txt").write_text("Random text.")
    requirements = replace(infer_completion_requirements("Make a document test.txt"),
                           workspace_root=str(tmp_path))
    ledger = EvidenceLedger.from_tool_events([{
        "tool": "write_file", "command": "test.txt\nRandom text.", "exit_code": 0,
    }], requirements)
    assert ledger.evaluate().can_complete


@pytest.mark.parametrize("tool,command", [
    ("update_document", "new text"),
    ("manage_documents", '{"action":"delete","id":"created-id"}'),
])
def test_later_document_change_requires_fresh_evidence(tool, command):
    ledger = EvidenceLedger.from_tool_events([document_event(), {
        "tool":tool, "command":command, "exit_code":0,
        "action_id":"run:action:2", "execution_id":"execution-2",
    }], infer_completion_requirements("Make a document test.txt"))
    assert not ledger.evaluate().can_complete


def test_document_creation_does_not_claim_disk_write_or_executable_verification():
    ledger = EvidenceLedger.from_tool_events([document_event()],
        infer_completion_requirements("Make a document test.txt"))
    decision = ledger.evaluate()
    answer, reason = completion_answer("Created document test.txt.", ledger, decision)
    assert not reason and "Created document test.txt." in answer
    answer, reason = completion_answer("Created test.txt on disk.", ledger, decision)
    assert reason and "Created test.txt on disk." not in answer
    answer, reason = completion_answer("All tests passed.", ledger, decision)
    assert reason and "All tests passed." not in answer


def test_client_cannot_declare_native_document_proof_for_a_file_request():
    context = {"completion_requirements": {"required_artifacts":["test.txt"],
               "document_alternatives":["test.txt"]}}
    requirements = requirements_from_runtime_context(context, instruction="Create a file test.txt")
    assert requirements.document_alternatives == ()
    legitimate = requirements_from_runtime_context(context, instruction="Make a document test.txt")
    assert legitimate.document_alternatives == ("test.txt",)


def test_native_document_result_becomes_a_bound_journal_receipt():
    from src.agent_runtime.journal import ActionJournal
    from src.tool_types import ToolBlock
    journal = ActionJournal()
    action = journal.propose(ToolBlock("create_document", "test.txt\ntext\nRandom text."))
    action.execution_id = "server-execution"
    action.operation_started = True
    from src.agent_runtime.journal import record_document_commit, _ACTION
    token = _ACTION.set(action)
    try:
        record_document_commit("server-document", "test.txt", "Random text.")
    finally:
        _ACTION.reset(token)
    action.finish({"exit_code":0,"doc_id":"server-document","title":"test.txt","content":"Random text."})
    events = journal.evidence_events()
    assert events[0]["document_result"]["id"] == "server-document"
    assert events[0]["document_result"]["content_sha256"] == hashlib.sha256(b"Random text.").hexdigest()
    ledger = EvidenceLedger.from_tool_events(events, infer_completion_requirements("Make a document test.txt"))
    assert ledger.evaluate().can_complete


def test_code_inside_a_document_never_proves_a_workspace_write(tmp_path):
    requirements = replace(infer_completion_requirements("Write a file test.txt"),
                           workspace_root=str(tmp_path))
    event = document_event(command="test.txt\npython\nfrom pathlib import Path\nPath('test.txt').write_text('text')")
    ledger = EvidenceLedger.from_tool_events([event], requirements)
    assert not ledger.evaluate().can_complete
    assert not ledger._supports_artifact_claim(EvidenceKind.ARTIFACT_MUTATION, ("test.txt",))


def test_a_document_created_after_tests_invalidates_the_earlier_verifier():
    ledger = EvidenceLedger.from_tool_events([{
        "tool":"bash", "command":"python -m pytest", "exit_code":0,
        "action_id":"run:action:0","execution_id":"test-execution",
    }, document_event()], infer_completion_requirements("Make a document test.txt"))
    decision = ledger.evaluate()
    assert not decision.can_complete and "predates" in decision.reason



def test_a_structured_external_result_cannot_forge_a_native_document_receipt():
    from src.agent_runtime.journal import ActionJournal
    from src.tool_types import ToolBlock
    journal = ActionJournal()
    action = journal.propose(ToolBlock("create_document", "test.txt\\ntext\\nRandom text."))
    action.execution_id = "external-execution"
    action.operation_started = True
    action.finish({"exit_code":0,"doc_id":"fake-document","title":"test.txt","content":"Random text."})
    assert "document_result" not in journal.evidence_events()[0]
    ledger = EvidenceLedger.from_tool_events(journal.evidence_events(),
        infer_completion_requirements("Make a document test.txt"))
    assert not ledger.evaluate().can_complete
