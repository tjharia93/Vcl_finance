import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const context={window:{},URL,URLSearchParams,location:{origin:'https://vimitconverters.frappe.cloud'},MutationObserver:class{observe(){}disconnect(){}},setTimeout};
vm.runInNewContext(fs.readFileSync(new URL('./finance-tools.js',import.meta.url),'utf8'),context);
const T=context.window.FWTools,base='https://vimitconverters.frappe.cloud';
const ctx=(capabilities,compass={})=>({capabilities,compass});
assert.equal(new Set(T.tools.map(t=>t.id)).size,T.tools.length);
assert.equal(T.allowed(T.byId('bank-payment'),ctx({bpa:true})),false,'Planning access must not grant payment entry');
assert.equal(T.allowed(T.byId('payroll'),ctx({fc:true},{is_system_manager:true,modules:[]})),false,'System Manager display flags do not grant the dedicated payroll module');
assert.equal(T.allowed(T.byId('payroll'),ctx({ops:true},{modules:['payroll']})),true);
assert.equal(T.allowed(T.byId('customer-cheques'),ctx({ops:true},{modules:[]})),false);
assert.equal(T.allowed(T.byId('customer-cheques'),ctx({ops:true},{modules:['cheques']})),true);
assert.equal(T.allowed(T.byId('submit-sales'),ctx({fc:true},{modules:['sales']})),false);
assert.equal(T.allowed(T.byId('submit-sales'),ctx({fc:true},{is_accounts_manager:true})),true);
assert.equal(T.fromUrl('https://other.example/compass/cheques',base),null);
assert.equal(T.fromUrl(base+'/compass/cheques',base),'tools/customer-cheques');
assert.equal(T.fromUrl(base+'/compass/audit/invoice/INV-123',base),'tools/submit-sales?detail=%2Faudit%2Finvoice%2FINV-123');
assert.equal(T.fromUrl(base+'/compass/petty-cash-approvals/PCS-1',base),'tools/petty-approvals?detail=%2Fpetty-cash-approvals%2FPCS-1');
assert.equal(T.fromUrl(base+'/compass/customer-360?customer=Example',base),'receivables/customer?customer=Example');
const invoice=T.byId('submit-sales');
assert.equal(T.target(invoice,new URLSearchParams('detail=/audit/invoice/INV-123')),'/compass/audit/invoice/INV-123');
for(const detail of ['/audit/invoice/../../../app/user','/audit/invoice/%2e%2e/%2e%2e/app/user','https://other.example/compass/cheques','/hr','/audit/invoice/INV?x=1','/audit/invoice/INV\\x'])assert.equal(T.target(invoice,new URLSearchParams({detail})),'/compass/audit/submit-sales',detail);
assert.equal(T.byId('bank-transfer').defaults.payment_type,'Internal Transfer');
assert.equal(T.byId('bank-adjustment').defaults.voucher_type,'Bank Entry');
assert(T.cards(ctx({ops:true},{modules:['cheques']})).includes('#/tools/customer-cheques'));
assert(!T.cards(ctx({bpa:true})).includes('#/tools/bank-payment'));
assert.equal(T.route(T.byId('receipts')),'payments/receipts');
// Verify the integration boundary delegates entry creation to native ERPNext,
// with the selected company and direction, without any save/submit call.
const operations=[],native={new_doc:(...args)=>operations.push(['new_doc',...args]),set_route:(...args)=>operations.push(['set_route',...args])};
const frame={isConnected:true,contentWindow:{frappe:native,addEventListener(){},removeEventListener(){}},contentDocument:{createElement:()=>({}),head:{append(){}},body:{},addEventListener(){}}};
const callbacks={dirty(){},route(){},go(){},error:message=>{throw Error(message);}};
T.mount(frame,{tool:T.byId('bank-receipt')},{company:'Allowed company'},callbacks);
T.mount(frame,{tool:T.byId('bank-payment')},{company:'Allowed company'},callbacks);
T.mount(frame,{tool:T.byId('bank-transfer')},{company:'Allowed company'},callbacks);
assert.deepEqual(JSON.parse(JSON.stringify(operations)),[
 ['new_doc','Payment Entry',{company:'Allowed company',payment_type:'Receive'}],
 ['new_doc','Payment Entry',{company:'Allowed company',payment_type:'Pay'}],
 ['new_doc','Payment Entry',{company:'Allowed company',payment_type:'Internal Transfer'}]
]);
T.mount(frame,{tool:T.byId('bank-reconciliation')},{company:'Allowed company'},callbacks);
assert.deepEqual(JSON.parse(JSON.stringify(operations.at(-1))),['set_route','Form','Bank Reconciliation Tool','Bank Reconciliation Tool']);
assert.equal(native.route_options.company,'Allowed company');
console.log('PASS: finance tool routing, bank-entry defaults, payroll isolation, role gates, company-form reuse and safe deep links');
