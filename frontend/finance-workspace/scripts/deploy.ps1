param([ValidateSet('Workspace','Redirects','Restore')][string]$Phase='Workspace',[string]$ExpectedModified='')
$ErrorActionPreference='Stop'
$financeRoot=Split-Path $PSScriptRoot
$financeSourceRoot=if($env:VCL_FINANCE_SOURCE){$env:VCL_FINANCE_SOURCE}else{Join-Path $financeRoot 'source'}
$financeHeaders=@{Authorization=('token '+$env:FRAPPE_API_KEY+':'+$env:FRAPPE_API_SECRET)}
$financeUrl=$env:FRAPPE_URL.TrimEnd('/')
function Read-FinancePage([string]$route){
    $financeSaved=Get-Content -LiteralPath (Join-Path $financeSourceRoot ($route.Replace('/','__')+'.json')) -Raw | ConvertFrom-Json
    $financeLive=(Invoke-RestMethod -Uri ($financeUrl+'/api/resource/Web%20Page/'+[uri]::EscapeDataString($financeSaved.name)) -Headers $financeHeaders).data
    return @{Saved=$financeSaved;Live=$financeLive}
}
function Write-FinancePage($original,$fields){
    $fields.modified=$original.modified
    $financeBody=ConvertTo-Json -InputObject $fields -Depth 15 -Compress
    $financeResult=Invoke-RestMethod -Method Put -Uri ($financeUrl+'/api/resource/Web%20Page/'+[uri]::EscapeDataString($original.name)) -Headers $financeHeaders -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($financeBody))
    $financeExpected=$fields.javascript
    if($financeResult.data.javascript -cne $financeExpected){throw 'JavaScript read-back mismatch for '+$original.route}
    [pscustomobject]@{route=$financeResult.data.route;modified=$financeResult.data.modified;verified=$true}
}
if($Phase -eq 'Workspace'){
    $financePage=Read-FinancePage 'finance-dev'
    if($ExpectedModified -and $financePage.Live.modified -ne $ExpectedModified){throw 'finance-dev changed after release review; reload and reconcile the change'}
    if($financePage.Live.modified -ne $financePage.Saved.modified -and $financePage.Live.main_section_html -notmatch 'data-build="2026-11-12-(lightning-1|vcl-roles-1|native-1)"'){throw 'finance-dev changed since backup; review before deployment'}
    $financeBackupDir=Join-Path $financeRoot 'release-backups'
    New-Item -ItemType Directory -Path $financeBackupDir -Force | Out-Null
    $financeBackup=Join-Path $financeBackupDir ('finance-dev-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.json')
    $financePage.Live | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $financeBackup -Encoding utf8
    Write-FinancePage $financePage.Live @{
        title='VCL Finance (dev)';main_section_html=(Get-Content -LiteralPath (Join-Path $financeRoot 'dist\finance-workspace.html') -Raw);
        javascript=(Get-Content -LiteralPath (Join-Path $financeRoot 'dist\finance-workspace.js') -Raw);dynamic_template=1;content_type='HTML'
    }
}
if($Phase -eq 'Redirects'){
    $financeCandidates=Get-ChildItem -LiteralPath $financeSourceRoot -Filter '*.json' | ForEach-Object {Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json} | Where-Object {$_.route -match '^fc-dev($|/)|^journal-dev$'}
    $financePending=@()
    foreach($financeOriginal in $financeCandidates){
        $financePage=Read-FinancePage $financeOriginal.route
        if($financePage.Live.modified -ne $financeOriginal.modified -and $financePage.Live.javascript -notmatch 'finance-workspace-redirect'){throw ('Concurrent edit on '+$financeOriginal.route)}
        $financePending+=$financePage
    }
    $financeRouteScript=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'routes.cjs') -Raw
    foreach($financePage in $financePending){
        $financeJs='/* finance-workspace-redirect 2026-11-12 */'+"`n"+$financeRouteScript+"`n"+'(function(){var target=FinanceRoutes.legacy(location.href);if(target!==null)location.replace("/finance-dev#/"+target);})();'
        Write-FinancePage $financePage.Live @{main_section_html='<p style="padding:32px;font:14px Segoe UI,sans-serif">This screen is now in the <a href="/finance-dev">Finance workspace</a>. Opening the matching tab…</p>';javascript=$financeJs;dynamic_template=1;content_type='HTML'}
    }
}
if($Phase -eq 'Restore'){
    foreach($financeFile in Get-ChildItem -LiteralPath $financeSourceRoot -Filter '*.json'){
        $financeOriginal=Get-Content -LiteralPath $financeFile.FullName -Raw | ConvertFrom-Json
        if($financeOriginal.route -notmatch '^finance-dev$|^fc-dev($|/)|^journal-dev$'){continue}
        $financePage=Read-FinancePage $financeOriginal.route
        if($financePage.Live.main_section_html -match 'data-build="2026-11-12-(lightning-1|vcl-roles-1|native-1)"' -or $financePage.Live.javascript -match '^/\* finance-workspace-redirect'){
            Write-FinancePage $financePage.Live @{main_section_html=$financeOriginal.main_section_html;javascript=$financeOriginal.javascript;title=$financeOriginal.title;dynamic_template=$financeOriginal.dynamic_template;content_type=$financeOriginal.content_type}
        }
    }
}
