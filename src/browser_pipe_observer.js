(() => {
  'use strict';
  // This object is retained by CDP in an isolated world. Nothing is installed
  // on the page's default global and no model text is interpreted as code.
  const capturedDocument = document;
  const attr = (e, name) => Element.prototype.getAttribute.call(e, name);
  const label = e => String(attr(e, 'aria-label') || (e.labels && [...e.labels].map(n => n.textContent).join(' ')) || e.innerText || attr(e, 'title') || attr(e, 'placeholder') || '').trim().slice(0, 160);
  const visible = e => {
    const box = e.getBoundingClientRect(), style = getComputedStyle(e);
    return e.ownerDocument === capturedDocument && e.isConnected && box.width > 0 && box.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const sensitive = e => /^(password|hidden|file)$/i.test(e.type || '') || /password|token|secret|credit.card|security.code/i.test(attr(e, 'name') || '');
  const fingerprint = e => ({label: label(e), role: attr(e, 'role'), type: e.type || '', href: e.tagName === 'A' ? e.href : attr(e, 'href'), target: attr(e, 'target') || (capturedDocument.querySelector('base[target]') || {}).target || '', download: attr(e, 'download'), form: e.form || null, action: e.form ? e.form.action : '', method: e.form ? e.form.method : ''});
  let token = '', nodes = new Map(), states = new Map(), navigationSerial = 0, capturedSerial = 0;
  // The Navigation API dispatches before same-document navigation commits.
  // URL comparison alone misses push/replace/back round trips to identical URLs.
  const navigationBound = typeof navigation !== 'undefined' && typeof navigation.addEventListener === 'function';
  const invalidate = () => { navigationSerial += 1; token = ''; };
  if (navigationBound) navigation.addEventListener('navigate', invalidate);
  for (const name of ['pagehide', 'pageshow', 'freeze', 'resume']) addEventListener(name, invalidate);
  // Trusted input from a human or the offscreen transport invalidates the
  // captured choice. Programmatic events emitted below are not trusted.
  for (const name of ['pointerdown', 'keydown', 'input', 'change', 'wheel']) capturedDocument.addEventListener(name, event => { if (event.isTrusted) invalidate(); }, true);
  const unchanged = (e, before) => {
    const now = fingerprint(e);
    return Object.keys(before).every(key => now[key] === before[key]);
  };
  const sameDocument = () => document === capturedDocument && capturedDocument.defaultView === window;
  return Object.freeze({
    snapshot(nonce) {
      if (!sameDocument()) return {error: 'stale_document'};
      if (!navigationBound) return {error: 'navigation_monitor_unavailable'};
      token = nonce; capturedSerial = navigationSerial; nodes = new Map(); states = new Map();
      const found = [];
      for (const e of capturedDocument.querySelectorAll('a[href],button,input,textarea,select,summary,[role=button],[role=link],[role=tab]')) {
        if (visible(e) && !sensitive(e)) found.push(e);
        if (found.length > 180) break;
      }
      const elements = found.slice(0, 180).map((e, index) => {
        const ref = 'n' + index; nodes.set(ref, e); states.set(ref, fingerprint(e));
        const destination = e.tagName === 'A' ? e.href : '';
        return {ref, label: label(e), role: attr(e, 'role') || e.tagName.toLowerCase(), editable: e.matches('input,textarea'), disabled: !!e.disabled || attr(e, 'aria-disabled') === 'true', destination, form_action: e.form ? e.form.action : '', form_method: e.form ? e.form.method : '', submits_form: !!e.form && (e.type === 'submit' || e.type === 'image'), in_viewport: e.getBoundingClientRect().top >= 0 && e.getBoundingClientRect().top < innerHeight};
      });
      return {nonce, url: location.href, title: capturedDocument.title.slice(0, 160), text: (capturedDocument.body ? capturedDocument.body.innerText : '').slice(0, 6000), elements, total_elements: found.length, truncated: found.length > elements.length, ready: capturedDocument.readyState};
    },
    action(args) {
      if (!sameDocument() || token !== args.nonce || capturedSerial !== navigationSerial) return {error: 'stale_observation'};
      if (args.action === 'scroll') { window.scrollBy(0, Math.max(-800, Math.min(800, args.amount))); token = ''; return {executed: true}; }
      const e = nodes.get(args.ref), before = states.get(args.ref);
      if (!e || !before) return {error: 'invalid_target'};
      if (!visible(e) || sensitive(e) || e.disabled || attr(e, 'aria-disabled') === 'true' || !unchanged(e, before)) return {error: 'target_unavailable'};
      if (before.download !== null || (before.target && before.target !== '_self')) return {error: 'target_unavailable'};
      const box = e.getBoundingClientRect(), hit = capturedDocument.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      if (!hit || !(hit === e || e.contains(hit))) return {error: 'target_covered'};
      // Consume before invoking page event handlers, which may navigate.
      token = '';
      if (args.action === 'click') { HTMLElement.prototype.click.call(e); return {executed: true}; }
      if (args.action !== 'fill' || !e.matches('input,textarea') || typeof args.text !== 'string' || args.text.length > 2000) return {error: 'unsupported_action'};
      const prototype = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
      setter.call(e, args.text); e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));
      return {executed: true};
    }
  });
})()
