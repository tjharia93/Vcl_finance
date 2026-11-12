(function(){
  const F=window.FC,E=F.esc;
  F.boot('/finance-dev',async()=>{
    const host=F.$('fc-body');host.innerHTML=F.state('Reading period locks…');
    try{
      const [periods,vouchers]=await Promise.all([
        F.list('Accounting Period',[],['name','start_date','end_date','company'],{order_by:'start_date desc',limit_page_length:100}),
        F.list('Period Closing Voucher',[],['name','docstatus','transaction_date','period_start_date','period_end_date'],{order_by:'period_end_date desc',limit_page_length:100})
      ]);
      const posted=vouchers.filter(v=>v.docstatus===1);
      host.innerHTML=(periods.length?'':'<div class="warnbox"><b>No accounting period is locked.</b> Review both books and ensure the sales sync respects closed dates before setting the first lock. This view reads existing records; it does not lock a period.</div>')+
        F.rcard('Accounting periods',periods.length?'<div class="twrap"><table class="tb"><thead><tr><th>Period</th><th>From</th><th>To</th><th>Company</th></tr></thead><tbody>'+periods.map(p=>'<tr><td><a href="'+E(F.deskUrl('Accounting Period',p.name))+'" target="_blank" rel="noopener">'+E(p.name)+'</a></td><td>'+F.dmy(p.start_date)+'</td><td>'+F.dmy(p.end_date)+'</td><td>'+E(p.company)+'</td></tr>').join('')+'</tbody></table></div>':'<div class="state">No accounting periods recorded.</div>')+
        F.rcard('Submitted period closing vouchers',posted.length?'<div class="twrap"><table class="tb"><thead><tr><th>Voucher</th><th>Date</th><th>Period</th></tr></thead><tbody>'+posted.map(p=>'<tr><td><a href="'+E(F.deskUrl('Period Closing Voucher',p.name))+'" target="_blank" rel="noopener">'+E(p.name)+'</a></td><td>'+F.dmy(p.transaction_date)+'</td><td>'+F.dmy(p.period_start_date)+' – '+F.dmy(p.period_end_date)+'</td></tr>').join('')+'</tbody></table></div>':'<div class="state">No submitted closing vouchers.</div>');
    }catch(e){host.innerHTML=F.errbox(e);}
  });
})();
