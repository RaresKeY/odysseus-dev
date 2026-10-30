"""Exact offered membership, hard payload caps and immutable input snapshots."""
import pytest
from src.automation_catalog import CatalogError, compact_catalog


def test_compact_catalog_retains_complete_offered_membership_without_payloads():
    state={'ask':'  Exact current ask λ  ','historical':{'previous_status':'completed','active_task':None}}
    descriptions={'HANDOFF':'Return control','TARGET_0':'click: Save'}
    offered={'HANDOFF':None,'TARGET_0':{'action':'click','observation':'opaque','ref':'n0'}}
    frozen_state,frozen_descriptions,frozen_offered=compact_catalog(state,descriptions,offered)
    assert set(frozen_descriptions)==set(frozen_offered)==set(offered)
    assert frozen_state['ask']==state['ask']
    state['ask']='mutated';offered['TARGET_0']['ref']='n1';descriptions['TARGET_0']='changed'
    assert frozen_state['ask']=='  Exact current ask λ  '
    assert frozen_offered['TARGET_0']['ref']=='n0'
    assert frozen_descriptions['TARGET_0']=='click: Save'


@pytest.mark.parametrize('side', ['descriptions','offered'])
def test_hidden_or_unresolvable_choice_is_refused(side):
    descriptions={'A':'one','B':'two'};offered={'A':None,'B':{'action':'snapshot'}}
    (descriptions if side=='descriptions' else offered)['HIDDEN']='hidden' if side=='descriptions' else {'action':'click'}
    with pytest.raises(CatalogError,match='membership'):compact_catalog({},descriptions,offered)


def test_utf8_budget_is_hard_and_does_not_silently_truncate_ask():
    raw='🙂'*100
    with pytest.raises(CatalogError,match='oversized'):
        compact_catalog({'ask':raw},{'A':'one','B':'two'},{'A':None,'B':None},max_request_bytes=200)
    assert raw=='🙂'*100


@pytest.mark.parametrize('value', [object(),float('inf'),(1,2),{1:'nonstringkey'}])
def test_live_or_non_json_operation_data_is_refused(value):
    with pytest.raises(CatalogError,match='invalid_json'):
        compact_catalog({}, {'A':'one','B':'two'}, {'A':None,'B':{'value':value}})
