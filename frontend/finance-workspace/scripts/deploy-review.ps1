$ErrorActionPreference='Stop'
$financeRoot=Split-Path $PSScriptRoot
$financeHeaders=@{Authorization=('token '+$env:FRAPPE_API_KEY+':'+$env:FRAPPE_API_SECRET)}
$financeUrl=$env:FRAPPE_URL.TrimEnd('/')
$financeCandidate=@{doctype='Web Page';title='VCL Finance native rebuild review';route='finance-native-review';published=1;full_width=1;show_title=0;dynamic_template=1;content_type='HTML';main_section_html=(Get-Content -LiteralPath (Join-Path $financeRoot 'dist\finance-workspace.html') -Raw);javascript=(Get-Content -LiteralPath (Join-Path $financeRoot 'dist\finance-workspace.js') -Raw)}
$financeExisting=(Invoke-RestMethod -Uri ($financeUrl+'/api/resource/Web%20Page?filters='+[uri]::EscapeDataString('[["route","=","finance-native-review"]]')) -Headers $financeHeaders).data
if($financeExisting.Count){$financeResult=Invoke-RestMethod -Method Put -Uri ($financeUrl+'/api/resource/Web%20Page/'+[uri]::EscapeDataString($financeExisting[0].name)) -Headers $financeHeaders -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes(($financeCandidate|ConvertTo-Json -Depth 8 -Compress)))}else{$financeResult=Invoke-RestMethod -Method Post -Uri ($financeUrl+'/api/resource/Web%20Page') -Headers $financeHeaders -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes(($financeCandidate|ConvertTo-Json -Depth 8 -Compress)))}
if($financeResult.data.javascript -cne $financeCandidate.javascript){throw 'Native review JavaScript read-back mismatch'}
[pscustomobject]@{route=$financeResult.data.route;modified=$financeResult.data.modified;verified=$true}
