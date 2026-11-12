/* Same-origin isolated tools retain their tested handlers and authenticated session. */
(function () {
  'use strict';
  const config = window.FINANCE_CONFIG;
  const post = (type, data) => parent.postMessage({financeWorkspace:true, key:config.key, type, ...data}, parent.location.origin);
  window.FINANCE_QUERY = config.query || '';
  window.FINANCE_HASH = config.initialHash || '#/';
  window.frappe = Object.create(parent.frappe || {});
  Object.defineProperty(frappe, 'csrf_token', {get:()=>parent.frappe && parent.frappe.csrf_token});
  frappe.ready = fn => { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn, {once:true}); else queueMicrotask(fn); };
  if (parent.frappe && parent.frappe.call) frappe.call = parent.frappe.call.bind(parent.frappe);
  // srcdoc cannot rewrite its URL to the <base> URL. The parent owns history;
  // original hash-based tools read this virtual hash instead.
  history.replaceState = function (state, unused, url) {
    if (!url) return;
    if (String(url).startsWith('#')) { window.FINANCE_HASH=String(url); return; }
    const next = new URL(String(url), document.baseURI);
    window.FINANCE_QUERY = next.search;
    post('params', {query:next.search});
  };
  window.financeNavigate = route => post('navigate', {route});
  let dirty = false;
  function setDirty(value) { dirty = value; post('dirty', {dirty}); }
  document.addEventListener('input', e => {
    if (config.view === 'journal' && e.target.closest('#jd-root') && !e.target.matches('#jd-open,#jd-qj')) setDirty(true);
    if (config.view === 'receipts' && e.target.closest('form')) setDirty(true);
  }, true);
  document.addEventListener('change', e => {
    if (config.view === 'journal' && e.target.closest('#jd-root')) setDirty(true);
  }, true);
  document.addEventListener('click', e => {
    const anchor = e.target.closest('a[href]');
    if (!anchor || anchor.hasAttribute('download') || e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const raw = anchor.getAttribute('href');
    if (!raw || raw.startsWith('javascript:')) return;
    if (raw.startsWith('#') && config.view === 'finance') {
      e.preventDefault(); e.stopImmediatePropagation();
      post('navigate', {route:FinanceRoutes.normalize(raw)}); return;
    }
    if (raw.startsWith('#')) return;
    const u = new URL(raw, document.baseURI);
    if (u.origin === parent.location.origin) {
      const route = FinanceRoutes.legacy(u.href);
      if (route !== null && anchor.target !== '_blank') {
        e.preventDefault(); e.stopImmediatePropagation(); post('navigate', {route}); return;
      }
    }
    if (anchor.target !== '_blank') anchor.target = '_top';
    if (anchor.target === '_blank') anchor.rel = 'noopener';
  }, true);
  window.addEventListener('message', e => {
    if (e.source !== parent || !e.data || !e.data.financeWorkspace) return;
    if (e.data.type === 'finance-route') {
      if (window.FINANCE_HASH !== e.data.hash) {
        window.FINANCE_HASH=e.data.hash;
        window.dispatchEvent(new HashChangeEvent('hashchange'));
      }
    }
    if (e.data.type === 'deactivate' && config.view === 'journal') {
      const read = document.querySelector('#jd-seg [data-m="read"]');
      if (read) read.click(); // A background journal never stays in writing mode.
    }
    if (e.data.type === 'refresh-drafts' && window.financeRefreshDrafts) window.financeRefreshDrafts();
  });
  const watch = new MutationObserver(changes => {
    // Clearing or saving in the original editor resets only the unsaved-change guard.
    const out = document.querySelector('#jd-out');
    if (config.view === 'journal' && dirty && out && changes.some(change=>change.target===out||out.contains(change.target)) && /Saved as draft|Submitted\. ERPNext/.test(out.textContent)) setDirty(false);
    if (config.view === 'journal' && dirty && !document.querySelector('#jd-lines tbody tr') && !document.querySelector('#jd-rem')?.value) setDirty(false);
  });
  document.addEventListener('DOMContentLoaded', () => {
    watch.observe(document.body,{subtree:true,childList:true});
    post('ready',{});
  },{once:true});
  window.addEventListener('error', e => post('tool-error',{message:e.message}));
})();
