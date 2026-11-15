// Browser-only regression probe. The caller supplies the real renderer and chat root.
export async function verifyMessageFooterState(renderer, root) {
  if (!root?.isConnected) throw new Error('A connected disposable chat root is required');
  const results = [];
  for (const mode of ['prepared-legacy', 'connected-move', 'connected-legacy']) {
    const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'moveBefore');
    const nativeMove = Element.prototype.moveBefore;
    const previousFocus = document.activeElement;
    const message = document.createElement('div');
    message.className = 'msg msg-ai';
    const body = document.createElement('div');
    body.className = 'body';
    const input = document.createElement('input');
    input.value = 'unsent draft';
    const frame = document.createElement('iframe');
    const loaded = new Promise(resolve => frame.addEventListener('load', resolve, { once: true }));
    frame.src = 'about:blank';
    body.append(input, frame);
    message.append(body);
    try {
      if (mode !== 'connected-move') Element.prototype.moveBefore = undefined;
      if (mode === 'prepared-legacy') renderer.prepareMessagePlate(message);
      root.append(message);
      await loaded;
      frame.contentWindow.plateProbe = 123;
      input.focus();
      message.append(renderer.createMsgFooter(message));
      // A detached/reinserted frame reloads asynchronously, after append returns.
      await new Promise(resolve => setTimeout(resolve, 100));
      const expectedPlate = mode === 'prepared-legacy' ||
        (mode === 'connected-move' && typeof nativeMove === 'function');
      const result = {
        mode,
        focused: document.activeElement === input,
        draftPreserved: input.value === 'unsent draft',
        framePreserved: frame.contentWindow.plateProbe === 123,
        layoutPreserved: !!message.querySelector(':scope > .msg-plate') === expectedPlate,
        singleFooter: message.querySelectorAll('.msg-footer').length === 1,
      };
      result.passed = result.focused && result.draftPreserved && result.framePreserved &&
        result.layoutPreserved && result.singleFooter;
      results.push(result);
    } finally {
      if (descriptor) Object.defineProperty(Element.prototype, 'moveBefore', descriptor);
      else delete Element.prototype.moveBefore;
      message.remove();
      if (previousFocus?.isConnected) previousFocus.focus();
    }
  }
  return results;
}
