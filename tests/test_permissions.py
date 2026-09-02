from jarvis.core.permissions import PermissionPolicy, ProposedAction, RiskLevel


def action(risk: RiskLevel) -> ProposedAction:
    return ProposedAction(tool_name="test", risk=risk, summary="test", payload={"id": 1})


def test_deterministic_default_policy() -> None:
    policy = PermissionPolicy()

    assert not policy.requires_confirmation(action(RiskLevel.READ_ONLY))
    assert not policy.requires_confirmation(action(RiskLevel.REVERSIBLE))
    assert policy.requires_confirmation(action(RiskLevel.EXTERNAL_WRITE))
    assert policy.requires_confirmation(action(RiskLevel.DESTRUCTIVE))


def test_reversible_actions_can_be_configured_to_confirm() -> None:
    assert PermissionPolicy(confirm_reversible=True).requires_confirmation(
        action(RiskLevel.REVERSIBLE)
    )

