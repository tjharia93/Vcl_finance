/* Shared, pure routing contract for the workspace, embedded tools and legacy redirects. */
(function (root) {
  const aliases = {ar:'receivables',c360:'receivables/customer',cheques:'payments/cheques',run:'payments/run',unpresented:'payments/unpresented',book:'payments/book',petty:'payments/petty','petty-week':'payments/petty-week','petty-grid':'payments/petty-grid','petty-new':'payments/petty-new','petty-import':'payments/petty-import',mpesa:'payments/receipts'};
  const old = {
    '/fc-dev':'close', '/fc-dev/close':'close', '/fc-dev/journals':'journals',
    '/fc-dev/postings':'payments/cheques', '/fc-dev/reports':'reports',
    '/fc-dev/chart':'setup/chart', '/fc-dev/cost-centres':'setup/cost-centres',
    '/fc-dev/items':'setup/items', '/fc-dev/budget':'reports/budget',
    '/fc-dev/prototypes':'setup/prototypes', '/journal-dev':'journals/edit',
    '/receipts-dev':'payments/receipts', '/petty-cash-dev':'payments/petty-posting',
    '/recon-dev':'close/allocate', '/erp-qbo-sync-dev':'close/reconciliation'
  };
  function normalize(input) {
    let value = String(input || '').replace(/^#?\/?/, '');
    const i = value.indexOf('?'), path = i < 0 ? value : value.slice(0,i), query = i < 0 ? '' : value.slice(i);
    return (aliases[path] || path || 'cash') + query;
  }
  function legacy(input, base) {
    const u = new URL(input, base || 'https://vimitconverters.frappe.cloud/finance-dev');
    const pathname = u.pathname.replace(/\/$/,'');
    if (pathname === '/finance-dev') return normalize(u.hash || ('cash' + u.search));
    if (!Object.prototype.hasOwnProperty.call(old, pathname)) return null;
    let route = old[pathname];
    if (pathname === '/fc-dev/journals') {
      route = {'#lock':'close/locks','#pi':'payments/draft-purchases','#pe':'payments/draft-payments','#si':'receivables/drafts'}[u.hash] || 'journals';
    }
    if (pathname === '/fc-dev/postings') route = {'#cust':'cash','#supp':'payments/cheques','#petty':'payments/petty','#pe':'payments/draft-payments','#je':'journals'}[u.hash] || route;
    return route + u.search;
  }
  const api = {normalize, legacy, old};
  if (typeof module !== 'undefined') module.exports = api;
  else root.FinanceRoutes = api;
})(typeof window === 'undefined' ? {} : window);
