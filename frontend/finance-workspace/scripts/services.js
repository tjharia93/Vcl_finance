(function(){
 'use strict';
 const NS='vcl_finance.workspace.api.';
 const clean=v=>v===null||v===undefined?'':String(v);
 async function call(method,args={},post=false){
  const headers={Accept:'application/json'};let url='/api/method/'+method,options={credentials:'same-origin',headers};
  if(post){let token='';for(let i=0;i<12;i++){token=window.frappe?.csrf_token||window.csrf_token||'';if(token&&token!=='None'&&!token.includes('{'))break;await new Promise(r=>setTimeout(r,200));}
   if(!token||token==='None')throw Error('Your session is still initialising. Try again.');
   options.method='POST';headers['Content-Type']='application/json';headers['X-Frappe-CSRF-Token']=token;options.body=JSON.stringify(args);
  }else url+='?'+new URLSearchParams(Object.entries(args).filter(([,v])=>v!==undefined&&v!==null).map(([k,v])=>[k,typeof v==='string'?v:JSON.stringify(v)]));
  const r=await fetch(url,options),j=await r.json().catch(()=>({}));
  if(!r.ok){let msg=j.exception||j._error_message||'Request failed ('+r.status+').';try{const s=JSON.parse(j._server_messages||'[]');if(s.length)msg=JSON.parse(s[0]).message.replace(/<[^>]+>/g,'');}catch{}
   const e=Error(msg);e.status=r.status;e.code=j.exc_type||'';throw e;}
  return j.message;
 }
 async function list(dt,filters,fields,start=0,size=1000,order='name'){return call('frappe.client.get_list',{doctype:dt,filters,fields,limit_start:start,limit_page_length:size,order_by:order});}
 async function all(dt,filters,fields){let rows=[],start=0;for(;;){const page=await list(dt,filters,fields,start);rows.push(...page);if(page.length<1000)return rows;start+=page.length;}}
 async function capabilities(company){try{return {...await call(NS+'capabilities',{company}),backend:true};}catch(e){
   if(e.status===401||e.status===403)throw e;
   if(!/ModuleNotFoundError|ImportError|AttributeError|not found|Failed to get method|not installed|DoesNotExistError|Finance phase 1/i.test(e.message+' '+e.code))throw e;
   const access=await call('vcl_compass.api.access.get_my_modules');
   const companies=await list('Company',[],['name','default_currency'],0,100);
   const owner=!!access.is_system_manager,fc=owner||!!access.is_accounts_manager,ops=fc||(access.modules||[]).some(x=>['petty_cash','supplier_cheques'].includes(x));
   return {backend:false,user:access.user,capabilities:{fc,bpa:owner,ops,setup:fc},final_approver:false,companies,company:company||companies.find(x=>x.name==='Vimit Converters Limited')?.name||companies[0]?.name,currency:companies.find(x=>x.name===(company||'Vimit Converters Limited'))?.default_currency||companies[0]?.default_currency,phases:{1:true,2:false,3:false,4:false}};
  }}
 const config={je:['Journal Entry',['voucher_type','user_remark','total_debit']],pi:['Purchase Invoice',['supplier_name','bill_no','currency','grand_total','base_grand_total']],si:['Sales Invoice',['customer_name','currency','grand_total','base_grand_total']],pe:['Payment Entry',['payment_type','party_name','paid_amount','received_amount','base_paid_amount','base_received_amount','paid_from_account_currency','paid_to_account_currency']]};
 function summarize(rows,kind,currency){const totals={},tx={};for(const r of rows){const dir=r.payment_type||'Drafts',key=kind==='je'?'total_debit':kind==='pe'?(dir==='Pay'||dir==='Internal Transfer'?'base_paid_amount':'base_received_amount'):'base_grand_total';
   const b=totals[dir]??={count:0,base_amount:0,missing:0};b.count++;if(r[key]===null||r[key]===undefined)b.missing++;else b.base_amount+=Number(r[key]);
   const cur=r.currency||(['Pay','Internal Transfer'].includes(dir)?r.paid_from_account_currency:r.paid_to_account_currency)||currency,raw=kind==='pi'||kind==='si'?r.grand_total:kind==='je'?r.total_debit:['Pay','Internal Transfer'].includes(dir)?r.paid_amount:r.received_amount;
   const k=dir+'|'+cur;tx[k]=(tx[k]||0)+Number(raw||0);
  }for(const b of Object.values(totals))b.base_amount=b.missing?null:Math.round((b.base_amount+Number.EPSILON)*100)/100;
  return {base_currency:currency,directions:totals,transaction_totals:Object.entries(tx).map(([k,amount])=>({direction:k.split('|')[0],currency:k.split('|')[1],amount}))};}
 async function drafts(ctx,kind,start=0,query=''){
  if(ctx.backend)return call(NS+'drafts',{company:ctx.company,kind,start,page_length:30,query});
  const [dt,fields]=config[kind],rows=await all(dt,{company:ctx.company,docstatus:0},['name','posting_date','owner','modified',...fields]);
  rows.sort((a,b)=>clean(a.posting_date).localeCompare(clean(b.posting_date))||a.name.localeCompare(b.name));
  const filtered=query?rows.filter(r=>query.toLowerCase().split(/\s+/).every(w=>Object.values(r).join(' ').toLowerCase().includes(w))):rows;
  return {rows:filtered.slice(start,start+30),total:filtered.length,summary:summarize(filtered,kind,ctx.currency),basis:{company:ctx.company,currency:ctx.currency,source:'ERPNext',read_at:new Date().toISOString(),review_status:'Operational'}};
 }
 async function actuals(ctx,start,end){
  if(ctx.backend)return call(NS+'actuals',{company:ctx.company,period_start:start,period_end:end});
  const accounts=await all('Account',{company:ctx.company,is_group:0},['name','root_type']),types=Object.fromEntries(accounts.map(a=>[a.name,a.root_type]));
  const gl=await all('GL Entry',{company:ctx.company,is_cancelled:0,voucher_type:['!=','Period Closing Voucher'],posting_date:['between',[start,end]]},['name','account','cost_center','posting_date','debit','credit']);const groups={};let tagged=0,main=0;
  for(const r of gl){if(!['Income','Expense'].includes(types[r.account]))continue;const category=types[r.account]==='Income'?'Revenue':'Expense';if(r.cost_center){tagged++;if(r.cost_center.startsWith('Main -'))main++;}
   const key=[r.posting_date.slice(0,7),r.account,r.cost_center||'Unallocated',category].join('|');groups[key]=(groups[key]||0)+(category==='Revenue'?Number(r.credit)-Number(r.debit):Number(r.debit)-Number(r.credit));}
  const rows=Object.entries(groups).map(([k,amount])=>{const[period,account,cost_center,category]=k.split('|');return {period,account,cost_center,category,amount};});
  return {rows,totals:Object.fromEntries(['Revenue','Expense'].map(k=>[k,rows.filter(r=>r.category===k).reduce((s,r)=>s+r.amount,0)])),basis:{company:ctx.company,currency:ctx.currency,source:'ERPNext',period_start:start,period_end:end,read_at:new Date().toISOString(),review_status:'Provisional management'},quality:{tagged_lines:tagged,main_lines:main,product_profitability_ready:false,gaps:['Costs and cost-centre attribution require FC validation. QuickBooks statutory reporting is separate.']}};
 }
 async function queue(ctx){const result=ctx.backend?await call(NS+'work_queue',{company:ctx.company,own:ctx.workspace==='ops'?1:0}):{rows:[],note:'Assigned reviews and close tasks await the backend release. These are accessible transaction drafts, with no inferred review assignment.'};
  if(ctx.backend&&ctx.workspace!=='ops')return result;
  const reviewed=new Set(result.rows.filter(r=>r.kind==='Review').map(r=>r.title));
  for(const kind of ['je','pi','pe','si']){try{const filters={company:ctx.company,docstatus:0};if(ctx.workspace==='ops')filters.owner=ctx.user;const drafts=await all(config[kind][0],filters,['name','owner','posting_date']);for(const r of drafts)if(((ctx.workspace!=='ops'&&ctx.capabilities.fc)||r.owner===ctx.user)&&!(kind==='je'&&reviewed.has(r.name)))result.rows.push({name:r.name,title:r.name,kind:config[kind][0],owner:r.owner,due_date:null,status:'Draft',route:kind==='je'?'journals/edit?open='+encodeURIComponent(r.name):'ops/transactions?kind='+kind+'&query='+encodeURIComponent(r.name)});}catch(e){if(e.status!==403)throw e;}}
  return result;}

 window.FWServices={call,list,all,capabilities,drafts,actuals,queue,summarize,config,NS};
})();
