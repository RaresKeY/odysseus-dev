"""Keep private-browser pixels ephemeral while UI history stores a receipt."""
def screenshot_for_display(result):
    receipt = result.get('screenshot')
    if not isinstance(receipt, dict):
        receipt = (result.get('browser_handoff') or {}).get('screenshot') if isinstance(result.get('browser_handoff'), dict) else None
    if isinstance(receipt, dict):
        url = receipt.get('url')
        return url if isinstance(url, str) and url.startswith('/api/session/') and '/browser/image/' in url else None
    if 'browser_handoff' in result:
        return None
    # Existing media and legacy producers retain their current display path.
    images = result.get('images')
    image = images[0] if isinstance(images, list) and images else None
    if isinstance(image, dict) and (image.get('mimeType') or image.get('mime_type')) and image.get('data'):
        return f"data:{image.get('mimeType') or image['mime_type']};base64,{image['data']}"
    return None
