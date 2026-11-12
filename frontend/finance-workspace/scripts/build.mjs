import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {adaptJournal} from './journal-adapter.mjs';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url)), project=path.dirname(here);
const read=name=>fs.readFileSync(path.join(here,name),'utf8');
const sourceRoot=process.env.VCL_FINANCE_SOURCE||path.join(project,'source');
const source=route=>JSON.parse(fs.readFileSync(path.join(sourceRoot,route.replaceAll('/','__')+'.json'),'utf8').replace(/^\uFEFF/,''));
const common=source('fc-dev/journals').javascript.split('\n;\n')[0];
if(!common.includes('return { COMPANY:')||common.includes('var KINDS'))throw Error('FC common boundary changed');
const minimal=source('fc-dev/journals').main_section_html;
const views={};
const routeMap={finance:'finance-dev',journal:'journal-dev',reports:'fc-dev/reports',close:'fc-dev/close',chart:'fc-dev/chart',costCentres:'fc-dev/cost-centres',items:'fc-dev/items',budget:'fc-dev/budget',prototypes:'fc-dev/prototypes',receipts:'receipts-dev',pettyPost:'petty-cash-dev',reconcile:'recon-dev',controls:'erp-qbo-sync-dev'};
function adapt(js){
  return js.replace(/(?:window\.)?location\.search/g,'window.FINANCE_QUERY').replace(/(?:window\.)?location\.hash/g,'window.FINANCE_HASH').replaceAll('new URL(location.href)','new URL(document.baseURI + window.FINANCE_QUERY)');
}
for(const [view,route] of Object.entries(routeMap)){
  const doc=source(route);let js=adapt(doc.javascript||'');
  if(view==='journal')js=adaptJournal(js);
  if(view==='finance'){
    const marker="VFIN.start('vfin-app');";
    if(!js.includes(marker))throw Error('Finance startup boundary changed');
    // The workspace owns AR navigation and the one gross outstanding summary.
    js=js.replace("tile('Total outstanding', kes(d.total), (d.customer_count || 0) + ' customers') +", "(hasPdc ? '' : tile('Total outstanding', kes(d.total), (d.customer_count || 0) + ' customers')) +");
    js=js.replace("tile('QBO AR total', kes(c.qbo_total), 'as at ' + L.dmy(c.as_at)) +", "");
    js=js.replace(marker,`VFIN.landing.onRoute=function(){};VFIN.landing.update=function(){};
VFIN.ar.tabs=function(){return '';};
VFIN.go=function(p){window.financeNavigate(FinanceRoutes.normalize(p));};
VFIN.start('vfin-app');`);
  }
  if(view==='close'){
    // Keep live status checks but use links instead of repeating the operational totals.
    js=js.replace('"<b>" + n(r) + "</b> draft journals waiting"','"Review outstanding draft journals"');
    js=js.replace('"<b>" + n(rows.length) + "</b> cheques dated on or before month-end still in hand · KES " + F.money(sum)','"Review matured customer cheques in Cash & bank"');
    js=js.replace('href: R.postings','href: R.finance + "#/cash"');
    js=js.replace('"weeks ending in the month: <b>" + n(r[0]) + "</b> draft · <b>" + n(r[1]) + "</b> submitted · <b>" + n(r[2]) + "</b> approved · <b>" + n(r[3]) + "</b> closed"','"Review and post the month’s petty-cash sheets"');
  }
  views[view]={html:doc.main_section_html,js};
}
views.drafts={html:minimal,js:common+'\n;\n'+read('drafts.js')};
views.locks={html:minimal,js:common+'\n;\n'+read('locks.js')};
// Link state to parent navigation when programmatic finance screens change hash.
const bridge=read('frame-bridge.js');
for(const [key,view] of Object.entries(views))new vm.Script(view.js,{filename:key+'.js'});
const code='window.FINANCE_VIEWS='+JSON.stringify(views)+';\nwindow.FINANCE_EMBEDDED_CSS='+JSON.stringify(read('embedded.css'))+';\nwindow.FINANCE_ROUTES_SOURCE='+JSON.stringify(read('routes.cjs'))+';\nwindow.FINANCE_BRIDGE='+JSON.stringify(bridge)+';\n'+read('routes.cjs')+'\n'+read('services.js')+'\n'+read('native.js')+'\n'+read('workspace.js');
new vm.Script(code,{filename:'finance-workspace.js'});
new vm.Script(bridge,{filename:'frame-bridge.js'});
const html='<div id="finance-workspace" data-build="2026-11-12-vcl-roles-1"><p style="padding:32px">Loading Finance workspace…</p></div><style>'+read('workspace.css')+'</style>';
const dist=path.join(project,'dist');fs.mkdirSync(dist,{recursive:true});
fs.writeFileSync(path.join(dist,'finance-workspace.js'),code);
fs.writeFileSync(path.join(dist,'finance-workspace.html'),html);
fs.writeFileSync(path.join(dist,'manifest.json'),JSON.stringify({build:'2026-11-12-vcl-roles-1',views:Object.keys(views),bytes:Buffer.byteLength(code),sourceModified:Object.fromEntries(Object.values(routeMap).map(route=>[route,source(route).modified]))},null,2));
console.log(JSON.stringify({views:Object.keys(views).length,javascriptBytes:Buffer.byteLength(code),htmlBytes:Buffer.byteLength(html)}));
