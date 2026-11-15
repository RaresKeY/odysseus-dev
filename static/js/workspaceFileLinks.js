/** Local chat references only; the server owns workspace confinement. */
export function workspaceFilePath(value, {inline = false} = {}) {
  if (typeof value !== 'string' || !value || value.length > 4096 || /[\x00-\x1f\x7f]/.test(value)) return null;
  if (/^(?:#|\/\/|[a-z][a-z\d+.-]*:)/i.test(value)) return null;
  let path;
  try { path = inline ? value : decodeURIComponent(value.split(/[?#]/, 1)[0]); } catch (_) { return null; }
  if (!path || /[\x00-\x1f\x7f]/.test(path) || /^(?:#|\/\/|[a-z][a-z\d+.-]*:)/i.test(path) || path.startsWith('/api/') || path.startsWith('/static/')) return null;
  // Plain inline code needs to look like a filename; prose/code expressions stay code.
  if (inline && (!/\.[a-z\d]{1,12}$/i.test(path) || /[\n<>\x60|*?{}]/.test(path) || path.length > 512)) return null;
  return path;
}
export function fileLanguage(path) {
  const suffix = String(path).split('.').pop().toLowerCase();
  return ({md:'markdown',markdown:'markdown',txt:'text',py:'python',js:'javascript',mjs:'javascript',
    ts:'typescript',tsx:'typescript',jsx:'javascript',sh:'bash',yml:'yaml',htm:'html',rs:'rust'})[suffix] || suffix;
}
