import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const base=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../vcl_finance/finance_workspace');
const field=(fieldname,label,fieldtype,extra={})=>({fieldname,label,fieldtype,...extra});
const recordFields=[field('kind','Record type','Select',{options:'Review\nClose\nException\nPlan\nSnapshot\nPack',reqd:1,in_list_view:1}),
 field('title','Title','Data',{reqd:1,in_list_view:1}),field('company','Company','Link',{options:'Company',reqd:1,in_list_view:1}),
 field('status','Status','Select',{options:'Draft\nIn review\nChanges requested\nApproved\nPublished\nPosted\nCancelled\nResolved\nClosed',default:'Draft',reqd:1,in_list_view:1}),
 field('revision','Revision','Int',{default:1,read_only:1}),field('identity','Identity','Data',{unique:1,hidden:1}),
 field('source_doctype','Source type','Link',{options:'DocType',read_only:1}),field('source_document','Source document','Dynamic Link',{options:'source_doctype',read_only:1}),
 field('payload_json','Evidence and version data','Long Text',{reqd:1,read_only:1})];
const settingsFields=[field('final_approver','Final approver','Link',{options:'User',reqd:1}),...[1,2,3,4].map(n=>field('phase_'+n,'Release phase '+n,'Check',{default:n===1?1:0})),
 field('journal_review_required','Require independent journal review','Check'),field('sync_respects_locks','Backdated sync lock compatibility verified','Check'),
 field('release_attestation','Release acceptance evidence','Long Text'),field('reporting_gaps_json','Management reporting gaps (JSON)','Long Text'),field('dimension_rules_json','Future invoice dimension rules (JSON)','Long Text')];
for(const [name,fields,single]of [['VCL Finance Record',recordFields,0],['VCL Finance Workspace Settings',settingsFields,1]]){
 const slug=name.toLowerCase().replaceAll(' ','_'),dir=path.join(base,'doctype',slug);fs.mkdirSync(dir,{recursive:true});
 for(const p of [base,path.join(base,'doctype'),dir])fs.writeFileSync(path.join(p,'__init__.py'),'');
 const roles=single?['System Manager']:['System Manager','Accounts Manager','Accounts User','VCL Financial Controller','VCL Financial Planner','VCL Finance Operator'];
 const doc={doctype:'DocType',name,module:'Finance Workspace',custom:0,issingle:single,autoname:single?undefined:'format:FIN-.YYYY.-.#####',
  title_field:single?undefined:'title',track_changes:1,engine:'InnoDB',field_order:fields.map(f=>f.fieldname),fields,
  permissions:roles.map(role=>({role,read:1,select:1,report:1,print:single?0:1,export:single?0:1,create:single?1:0,write:single?1:0,delete:0})),sort_field:'modified',sort_order:'DESC'};
 fs.writeFileSync(path.join(dir,slug+'.json'),JSON.stringify(doc,null,2)+'\n');
 const cls=name.replaceAll(' ','');
 if(!fs.existsSync(path.join(dir,slug+'.py')))fs.writeFileSync(path.join(dir,slug+'.py'),single?`from frappe.model.document import Document\n\nfrom vcl_finance.workspace.guards import release_write_allowed\nimport frappe\n\nclass ${cls}(Document):\n    def validate(self):\n        if not release_write_allowed():\n            frappe.throw("Use the Finance release action with final authorisation and acceptance evidence.", frappe.PermissionError)\n`:
 `from frappe.model.document import Document\nfrom vcl_finance.workspace.guards import record_validate, record_delete\n\nclass ${cls}(Document):\n    def validate(self):\n        record_validate(self)\n\n    def on_trash(self):\n        record_delete(self)\n`);
}
console.log('Generated two controlled Finance Workspace DocTypes and their controllers.');
