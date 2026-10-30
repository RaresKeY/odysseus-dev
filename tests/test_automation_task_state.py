"""Historical-answer/pending separation adapted from Jev acceptance regressions."""
import pytest
from src.automation_task_state import (LifecycleError, pending_task_projection,
    submission_is_current, transition_status)


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_historical_answer_never_supplies_pending_question(status):
    raw = '  Original request λ  '
    state = pending_task_projection(raw, status, previous_answer='Yes? Continue?',
        pending_question={'question':'Historical question?'},pending_expires_at=500, now=100)
    assert state['previous_request'] == raw
    assert state['previous_answer'] == 'Yes? Continue?'
    assert state['active_task'] is None and state['pending_question'] is None


def test_only_live_unresolved_server_card_is_pending():
    card={'question':'Allow exactly this action?', 'approval_id':'opaque'}
    state=pending_task_projection('Original ask','awaiting_approval',pending_question=card,pending_expires_at=200,now=100)
    assert state['active_task']=='Original ask' and state['pending_question']==card
    card['question']='Changed outside the snapshot'
    assert state['pending_question']['question']=='Allow exactly this action?'
    for kwargs,expected in [({'pending_expires_at':100},'expired'),({'pending_question':{'question':'Allow?', 'resolved':True}},'resolved')]:
        options={'pending_question':card,'pending_expires_at':200,'now':100,**kwargs}
        state=pending_task_projection('Original ask','awaiting_approval',**options)
        assert state['previous_status']==expected
        assert state['active_task'] is None and state['pending_question'] is None


def test_preparation_requires_matching_live_finite_lease():
    assert submission_is_current('preparing',lease_token='one',expected_token='one',lease_expires_at=101,now=100)
    for expiry,token in [(100,'one'),(99,'one'),(float('inf'),'one'),(float('nan'),'one'),(101,'other')]:
        assert not submission_is_current('preparing',lease_token=token,expected_token='one',lease_expires_at=expiry,now=100)
    for status in ['bound','running','awaiting_approval']:
        assert submission_is_current(status,now=100)


@pytest.mark.parametrize('terminal', ['completed','failed','cancelled'])
def test_late_finally_cannot_revive_terminal_state(terminal):
    assert transition_status(terminal,'running')==terminal
    assert transition_status(terminal,'awaiting_approval')==terminal


def test_preparation_cannot_restart_after_admission():
    assert transition_status('preparing','bound')=='bound'
    assert transition_status('awaiting_approval','running')=='running'
    with pytest.raises(LifecycleError):transition_status('running','preparing')
    with pytest.raises(LifecycleError):transition_status('preparing','completed')
