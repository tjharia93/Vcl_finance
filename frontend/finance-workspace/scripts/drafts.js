(function () {
  const F=window.FC, E=F.esc, kind=window.FINANCE_CONFIG.kind || 'je';
  const kinds={
    je:{dt:'Journal Entry',title:'Draft journals',amount:'total_debit',fields:['name','posting_date','voucher_type','user_remark','total_debit'],cols:[['posting_date','Date'],['voucher_type','Type'],['user_remark','Remark'],['total_debit','Debit','n']]},
    pi:{dt:'Purchase Invoice',title:'Draft purchase invoices',amount:'grand_total',fields:['name','posting_date','supplier_name','bill_no','grand_total'],cols:[['posting_date','Date'],['supplier_name','Supplier'],['bill_no','Bill'],['grand_total','Total','n']]},
    pe:{dt:'Payment Entry',title:'Draft payment entries',amount:'paid_amount',fields:['name','posting_date','payment_type','party_name','paid_amount'],cols:[['posting_date','Date'],['payment_type','Type'],['party_name','Party'],['paid_amount','Amount','n']]},
    si:{dt:'Sales Invoice',title:'Draft sales invoices',amount:'grand_total',fields:['name','posting_date','customer_name','grand_total'],cols:[['posting_date','Date'],['customer_name','Customer'],['grand_total','Total','n']]}
  };
  const K=kinds[kind];
  async function load(){
    const host=F.$('fc-body'); host.innerHTML=F.state('Reading drafts…');
    try {
      const filters=[['docstatus','=',0]];
      const [rows,count]=await Promise.all([F.list(K.dt,filters,K.fields,{order_by:'posting_date asc',limit_page_length:500}),F.count(K.dt,filters)]);
      const total=rows.reduce((sum,row)=>sum+Number(row[K.amount]||0),0);
      host.innerHTML='<div class="fw-section-heading"><p>Drafts have not posted to the ledger. '+(kind==='je'?'Select a journal to open it in the editor.':'Select an entry to review it in ERPNext.')+'</p><button class="btn ghost" id="fw-reload">Refresh list</button></div>'+
        '<div class="fw-table-info"><b>'+E(count)+' drafts</b> · KES '+F.money(total)+(count>rows.length?' across '+rows.length+' loaded entries; more drafts exist in ERPNext.':'')+(rows.length?' · oldest '+F.dmy(rows[0].posting_date):'')+'</div><div id="fw-drafts"></div>';
      F.$('fw-reload').onclick=load;
      if(!rows.length){F.$('fw-drafts').innerHTML='<div class="ok">No '+E(K.title.toLowerCase())+' waiting.</div>';return;}
      F.paged(F.$('fw-drafts'),{rows,unit:'entries',placeholder:'Entry, party or remark…',search:x=>K.fields.map(f=>x[f]).join(' '),
        head:'<tr><th>Entry</th>'+K.cols.map(c=>'<th'+(c[2]?' class="n"':'')+'>'+E(c[1])+'</th>').join('')+'<th>Action</th></tr>',
        row:x=>'<tr><td><a href="'+E(kind==='je'?'/finance-dev#/journals/edit?open='+encodeURIComponent(x.name):F.deskUrl(K.dt,x.name))+'">'+E(x.name)+'</a></td>'+K.cols.map(c=>'<td data-label="'+E(c[1])+'"'+(c[2]?' class="n"':'')+'>'+(c[2]?F.money(x[c[0]]):c[0]==='posting_date'?E(F.dmy(x[c[0]])):E(String(x[c[0]]||'').slice(0,160)))+'</td>').join('')+'<td><a href="'+E(F.deskUrl(K.dt,x.name))+'" target="_blank" rel="noopener">ERPNext ↗</a></td></tr>'});
    } catch(e){host.innerHTML=F.errbox(e)+'<button class="btn ghost" id="fw-reload">Try again</button>';F.$('fw-reload').onclick=load;}
  }
  window.financeRefreshDrafts=load;
  F.boot('/finance-dev',load);
})();
