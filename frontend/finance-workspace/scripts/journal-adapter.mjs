export function adaptJournal(js){
 const swaps=[
  ['cost_center: F.MAIN','cost_center: l.cost_center || undefined'],
  ['account: r.account, ptype:','account: r.account, cost_center: r.cost_center || "", ptype:'],
  ['<th style="width:140px">Party type</th>','<th style="min-width:180px">Cost centre</th><th style="width:140px">Party type</th>'],
  ["'<td><select class=\"jl-pt\"", "'<td><input class=\"jl-cc\" data-i=\"' + i + '\" value=\"' + E(l.cost_center || '') + '\" placeholder=\"Choose cost centre\"></td><td><select class=\"jl-pt\""],
  ['<td colspan="3">Total','<td colspan="4">Total'],
  ['each(".jl-pt", function', 'each(".jl-cc", function(el,i){wireSuggest(el,"jd-dl-cc",function(){return "Cost Center";},function(){return {company:F.COMPANY,is_group:0};});el.addEventListener("change",function(){J.lines[i].cost_center=el.value.trim();gate();drawActions();});});\n    each(".jl-pt", function'],
  ['<datalist id="jd-dl-acc"></datalist>', '<datalist id="jd-dl-cc"></datalist><datalist id="jd-dl-acc"></datalist>'],
  ['["account_type", "account_currency", "is_group", "disabled"]','["account_type", "account_currency", "is_group", "disabled", "root_type"]'],
  ['if (d > 0 && c > 0) err.push', 'if (["Income","Expense"].indexOf(t.root_type)>=0 && !l.cost_center) err.push(n+"choose a cost centre for this income or expense line.");\n      if (d > 0 && c > 0) err.push'],
  ['Save and submit…','Submit to ERPNext…'],['Save and submit this journal?','Submit this journal to ERPNext?']
 ];
 for(const [from,to]of swaps){if(!js.includes(from))throw Error('Journal adaptation boundary changed: '+from.slice(0,60));js=js.replaceAll(from,to);}
 return js;
}
