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
await import('./verify-native.mjs');
console.log(JSON.stringify({legacyRouteCases:cases.length,nativeBuild:'pass'}));
