from jarvis import Jarvis
from jarvis.agents.email.schemas import EmailExecutionResult, EmailProposal


class FailingGmail:
    def save_draft(self, proposal: EmailProposal) -> EmailExecutionResult:
        return EmailExecutionResult(ok=False, operation="save_draft", error="OAuth expired")

    def send(self, proposal: EmailProposal) -> EmailExecutionResult:
        return EmailExecutionResult(ok=False, operation="send", error="OAuth expired")


def test_provider_failure_is_not_reported_as_success() -> None:
    jarvis = Jarvis(gmail=FailingGmail())
    result = jarvis.turn("Email Jisoo and say hello", thread_id="failure")
    result = jarvis.resume("send it", thread_id="failure")

    assert "couldn’t complete" in result.response
    assert "OAuth expired" in result.response
    assert result.response != "Sent."
    assert jarvis.state(thread_id="failure")["task_status"] == "failed"

