"""Browser pixels reach models transiently; persisted UI keeps an owner URL."""
from src.browser_visual_evidence import screenshot_for_display
from src.clean_agent_preview import bounded_visual_result_blocks


def test_private_pixels_are_not_the_durable_ui_screenshot():
    result={'images':[{'mimeType':'image/png','data':'SYNTHETIC_BYTES'}],
        'browser_handoff':{'screenshot':{'available':True,'url':'/api/session/chat/browser/image/opaque'}}}
    assert screenshot_for_display(result)=='/api/session/chat/browser/image/opaque'
    blocks=bounded_visual_result_blocks(result)
    assert blocks[0]['image_url']['url']=='data:image/png;base64,SYNTHETIC_BYTES'
    assert 'SYNTHETIC_BYTES' not in screenshot_for_display(result)


def test_private_handoff_without_valid_receipt_never_persists_pixels():
    result={'images':[{'mimeType':'image/png','data':'SYNTHETIC_BYTES'}],'browser_handoff':{'screenshot':{'available':False}}}
    assert screenshot_for_display(result) is None


def test_legacy_media_display_path_remains_available():
    assert screenshot_for_display({'images':[{'mimeType':'image/png','data':'legacy'}]})=='data:image/png;base64,legacy'
