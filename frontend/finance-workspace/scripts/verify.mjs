import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url)),require=createRequire(import.meta.url),R=require('./routes.cjs');
const cases=[
 ['/fc-dev','close'],['/fc-dev/close','close'],['/fc-dev/journals','journals'],['/fc-dev/journals#lock','close/locks'],
 ['/fc-dev/journals#pi','payments/draft-purchases'],['/fc-dev/journals#pe','payments/draft-payments'],['/fc-dev/journals#si','receivables/drafts'],
 ['/journal-dev?open=ACC-JV-2026-00906','journals/edit?open=ACC-JV-2026-00906'],
 ['/fc-dev/chart?tab=defaults','setup/chart?tab=defaults'],['/fc-dev/cost-centres?tab=map','setup/cost-centres?tab=map'],
 ['/fc-dev/reports?tab=tb','reports?tab=tb'],['/fc-dev/postings#petty','payments/petty'],['/fc-dev/postings#cust','cash'],
 ['/fc-dev/postings#je','journals'],['/fc-dev/postings#pe','payments/draft-payments'],
 ['/finance-dev#/petty-grid?s=PCS-2026-00044','payments/petty-grid?s=PCS-2026-00044'],
 ['/finance-dev#/petty-week?s=PCS-2026-00044','payments/petty-week?s=PCS-2026-00044'],
 ['/finance-dev#/petty-import','payments/petty-import'],['/finance-dev#/run','payments/run'],
 ['/receipts-dev?draft=ACC-PAY-2026-01234','payments/receipts?draft=ACC-PAY-2026-01234'],
 ['/recon-dev?type=Customer&party=Example','close/allocate?type=Customer&party=Example']
];
for(const [input,expected]of cases)assert.equal(R.legacy(input),expected,input);
assert.equal(R.legacy('/app/journal-entry/ACC-JV-2026-00906'),null);
const context={window:{}};const raw=fs.readFileSync(path.join(here,'../dist/finance-workspace.js'),'utf8');vm.runInNewContext(raw.slice(0,raw.indexOf('\n/* Shared, pure routing contract')),context);
const views=context.window.FINANCE_VIEWS;
assert(!views.home&&!views.postings,'Retired dashboards must not be included');
assert(views.finance.js.includes('VFIN.landing.onRoute=function(){}'),'No global metric card loads');
assert(!views.drafts.js.includes('var KINDS'),'Draft lists query just their owning document type');
assert(views.drafts.js.includes('/finance-dev#/journals/edit?open='),'Journal links must open the integrated editor');
assert(views.journal.js.includes('mode: "read"'),'Editor must default to read-only');
for(const snippet of ['async function save(thenSubmit)','function confirmSubmit()','frappe.client.submit','async function openDraft()','function payload()'])assert(views.journal.js.includes(snippet),snippet+' preserved');
assert(views.journal.js.includes('window.FINANCE_QUERY'),'Editor must use the workspace query');
assert(views.finance.js.includes('var h = (window.FINANCE_HASH'),'Finance must use parent-owned route state');
assert(!views.close.js.includes('"</b> draft journals waiting"'),'Close must link to draft journals without repeating counts');
for(const [key,view]of Object.entries(views))new vm.Script(view.js,{filename:key});
const bundle=fs.readFileSync(path.join(here,'../dist/finance-workspace.js'),'utf8');
assert(!bundle.includes('{{')&&!bundle.includes('{%'),'No literal Jinja expressions in a dynamic Web Page');
// A srcdoc bridge must boot without trying to replace history with its base URL.
const listeners={},docListeners={},sent=[],dispatched=[];
const bridgeContext={
 FINANCE_CONFIG:{view:'finance',key:'finance',initialHash:'#/cash',query:''},
 parent:{location:{origin:'https://vimitconverters.frappe.cloud'},frappe:{session:{user:'qa@example.com'}},postMessage:m=>sent.push(m)},
 location:{origin:'null'},history:{replaceState(){throw Error('srcdoc may not replace history with its base URL');}},
 document:{readyState:'loading',baseURI:'https://vimitconverters.frappe.cloud/finance-dev',addEventListener:(name,fn)=>docListeners[name]=fn},
 MutationObserver:class{observe(){}},HashChangeEvent:class{constructor(type){this.type=type;}},URL,
 addEventListener:(name,fn)=>listeners[name]=fn,dispatchEvent:event=>dispatched.push(event.type)
};bridgeContext.window=bridgeContext;
vm.runInNewContext(context.window.FINANCE_BRIDGE,bridgeContext);
assert.equal(bridgeContext.FINANCE_HASH,'#/cash');assert.equal(typeof docListeners.click,'function');
listeners.message({source:bridgeContext.parent,data:{financeWorkspace:true,type:'finance-route',hash:'#/ar'}});
assert.equal(bridgeContext.FINANCE_HASH,'#/ar');assert.deepEqual(dispatched,['hashchange']);
bridgeContext.financeNavigate('journals');assert.equal(sent.at(-1).route,'journals');
console.log(JSON.stringify({routeCases:cases.length,views:Object.keys(views).length,syntax:'pass',preservedJournalHandlers:'pass',retiredDashboardChecks:'pass',srcdocRouteBridge:'pass',jinja:'pass'}));
