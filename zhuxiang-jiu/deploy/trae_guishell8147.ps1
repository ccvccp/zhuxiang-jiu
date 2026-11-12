# Read-only probe: can a GUI desktop (Desktop Experience) be added to this box IN PLACE?
# Sentinel: GUISHELL8147-DONE
#
# QUESTION: the user RDPs into 8.147.66.237 and lands in a bare cmd window, then asks how to
# "get a desktop". Earlier probes on this box recorded "Windows Server 2025 Datacenter Server
# Core, explorer/mmc ABSENT", and probe VISUALPROBE8147 found an unconsumed HKLM RunOnce entry
# (Unattend0000000020, Start_ShowRun) -- RunOnce only fires on an interactive logon, and a
# Server Core box has none, so it is stranded forever. That is consistent with Server Core, but
# consistency is not proof, and the remedy depends entirely on which of two worlds we are in.
#
# DOCTRINE UNDER TEST: Microsoft removed the ability to convert Server Core <-> Server with
# Desktop Experience in place starting with Windows Server 2016 (2012 R2 was the last release
# that supported it), so the only route to a real desktop would be a re-image -- and re-imaging
# is on the user's explicit forbidden list, and would also wipe the single 39.9 GB system disk
# holding nginx8147 / redis8147 / backend8147 / the VisualSearch scheduled task / C:\backend /
# C:\www\visual / .env / the TLS certs. Before recommending "you cannot", read it off the box.
#
# WHAT WOULD FALSIFY THE DOCTRINE -- any one of these being positive:
#   [2/8] ServerLevels carrying a Server-Gui-Shell or Server-Gui-Mgmt-Infra value
#   [3/8] Get-WindowsFeature resolving either name with InstallState = Available
#   [4/8] Get-WindowsOptionalFeature listing an installable (Disabled) shell/desktop feature
#   [7/8] Get-WindowsCapability exposing a shell/desktop package
# All four negative => in-place GUI installation is impossible on this image.
#
# FAIL-CLOSED VERDICT RULE: [8/8] prints INPLACE-GUI-NOT-INSTALLABLE only when every decisive
# check returned a definite negative. If any of them threw, or if the positive control in [3/8]
# failed (meaning Get-WindowsFeature itself is broken, so "empty result" would be indistinguishable
# from "feature absent"), the verdict is UNKNOWN -- never a confident "impossible" built on a
# cmdlet that errored out.
#
# THIS SCRIPT PERFORMS NO WRITE. READ-ONLY-ASSERT: it reads registry keys, queries CIM, runs
# read-only ServerManager / DISM enumerations, does Test-Path and Get-Command lookups, and writes
# only to stdout. [1/8] and [8/8] stamp two files before and after so "no write" is falsifiable
# from the output alone.
# READ-ONLY-CAVEAT-1: the cloud assistant wrapper materialises a copy of this script under
#   $env:TEMP before running it, so "no write" is true of the script body, not of the transport.
# READ-ONLY-CAVEAT-2: under the cloud assistant HKCU resolves to the SYSTEM account hive, not the
#   RDP user's hive, so anything read from HKCU describes the SYSTEM side only.
# READ-ONLY-CAVEAT-3: DISM-backed cmdlets (Get-WindowsOptionalFeature / Get-WindowsCapability)
#   append to C:\Windows\Logs\DISM\dism.log and the CBS log as a subsystem side effect. This
#   script issues no write of its own, but the box is not byte-identical afterwards.
#
# OUTPUT BUDGET (probe #1 on this box blew the ~24 KB Output cap and silently lost its tail
# because a wide Get-WinEvent query matched 971 events): every enumeration below is filtered
# before it is formatted and capped by a constant, and no Get-WinEvent query appears anywhere.
$ErrorActionPreference = 'Stop'
$Sentinel = 'GUISHELL8147-DONE'
$Self     = $MyInvocation.MyCommand.Path
$OsProxy  = 'C:\Windows\System32\ntdll.dll'
$CapFeat  = 24
$CapCap   = 14
$CapBin   = 26
$CapLine  = 200

# Explicit desktop-GUI whitelist. Loose words (Gui|Shell|Desktop|Explorer|FullServer|
# MediaFoundation) matched Browser.InternetExplorer, WindowsPowerShellWebAccess,
# FailoverCluster-FullServer, ServerMediaFoundation and faked VERDICT=CANDIDATE-FOUND.
$GuiLevelNames   = @('Server-Gui-Shell', 'Server-Gui-Mgmt-Infra')
$GuiFeatureNames = @('Server-Gui-Shell', 'Server-Gui-Mgmt-Infra', 'Server-Gui-Shell-Package',
                     'Desktop-Experience', 'ServerCore-FullServer')

# Anchored whitelist; capability names lose their ~~~~0.0.11.0 suffix before comparison.
function InGuiWhite([string]$n, [string[]]$white) {
    if ([string]::IsNullOrEmpty($n)) { return $false }
    $base = ($n -replace '~~~~.*$', '')
    if ($base -imatch '^Server-Gui-') { return $true }
    foreach ($w in $white) { if ($base -ieq $w) { return $true } }
    return $false
}
$preSelf  = 'ABSENT'
$preOs    = 'ABSENT'

# tri-state evidence flags: 'neg' / 'pos:<detail>' / 'err:<detail>'
$ev2 = 'err:not-run'
$ev3 = 'err:not-run'
$ev4 = 'err:not-run'
$ev7 = 'err:not-run'
$evCtrl = 'err:not-run'

function Esc([string]$s) {
    if ($null -eq $s) { return '' }
    $sb = New-Object Text.StringBuilder
    foreach ($ch in $s.ToCharArray()) {
        $code = [int]$ch
        if ($code -gt 126 -or ($code -lt 32 -and $code -ne 10)) {
            [void]$sb.Append('\u' + $code.ToString('x4'))
        } elseif ($code -eq 10) {
            [void]$sb.Append('\n')
        } else { [void]$sb.Append($ch) }
    }
    return $sb.ToString()
}
function Clip([string]$s, [int]$n) {
    if ($null -eq $s) { return '' }
    if ($s.Length -le $n) { return $s }
    return $s.Substring(0, $n) + '...'
}
function StampOf([string]$path) {
    try {
        $fi = Get-Item -LiteralPath $path -ErrorAction Stop
        return $fi.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss.fff') + '|' + $fi.Length
    } catch { return 'ABSENT' }
}

Write-Output ('localNow=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Output ('host=' + $env:COMPUTERNAME)
Write-Output ('sentinel=' + $Sentinel)

# ---- [1/8] no-write baseline, taken before anything touches the box
Write-Output '[1/8] nowrite-pre'
$preSelf = StampOf $Self
$preOs   = StampOf $OsProxy
Write-Output ('  selfPath=' + (Esc $Self))
Write-Output ('  selfStamp=' + $preSelf)
Write-Output ('  osProxy=' + $OsProxy + ' stamp=' + $preOs)

# ---- [2/8] ServerLevels: THE authoritative marker of which installation option was used.
# A Desktop Experience install carries Server-Gui-Shell=1 and Server-Gui-Mgmt-Infra=1 next to
# ServerCore; a Server Core install carries ServerCore alone.
Write-Output '[2/8] server-levels-and-edition'
try {
    $cvPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $cv = Get-ItemProperty -LiteralPath $cvPath -ErrorAction Stop
    foreach ($n in @('ProductName','EditionID','CompositionEditionID','InstallationType','CurrentBuild','UBR','DisplayVersion','ReleaseId','ProductNameSurrogate1','ProductNameSurrogate2')) {
        $v = $cv.PSObject.Properties[$n]
        if ($v) { Write-Output ('  cv.' + $n + '=' + (Esc ([string]$v.Value))) }
        else { Write-Output ('  cv.' + $n + '=ABSENT') }
    }
} catch { Write-Output ('  UNKNOWN CurrentVersion: ' + (Esc $_.Exception.Message)) }
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    Write-Output ('  os.Caption=' + (Esc $os.Caption))
    Write-Output ('  os.Version=' + $os.Version + ' build=' + $os.BuildNumber + ' arch=' + $os.OSArchitecture)
    Write-Output ('  os.OSType=' + $os.OSType + ' productType=' + $os.ProductType + ' suiteMask=' + $os.SuiteMask)
    Write-Output ('  os.InstallDate=' + $os.InstallDate + ' lastBoot=' + $os.LastBootUpTime)
} catch { Write-Output ('  UNKNOWN Win32_OperatingSystem: ' + (Esc $_.Exception.Message)) }
try {
    $lvPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Server\ServerLevels'
    if (Test-Path -LiteralPath $lvPath) {
        $lv = Get-Item -LiteralPath $lvPath -ErrorAction Stop
        $names = @($lv.GetValueNames() | Where-Object { $_ })
        Write-Output ('  ServerLevels.valueNames=' + ($names -join ','))
        $guiHit = @()
        foreach ($n in $names) {
            $val = $lv.GetValue($n)
            Write-Output ('  ServerLevels.' + $n + '=' + $val)
            if (InGuiWhite $n $GuiLevelNames) { $guiHit += ($n + '=' + $val) }
        }
        if ($guiHit.Count -gt 0) { $ev2 = 'pos:' + ($guiHit -join ';') } else { $ev2 = 'neg' }
    } else {
        Write-Output '  ServerLevels.key=ABSENT'
        $ev2 = 'neg'
    }
} catch { Write-Output ('  UNKNOWN ServerLevels: ' + (Esc $_.Exception.Message)); $ev2 = 'err:' + (Esc (Clip $_.Exception.Message 120)) }

# ---- [3/8] ServerManager: do the 2012-era GUI feature names still resolve at all?
# Web-Mgmt-Console is the POSITIVE CONTROL: if it also comes back unresolved, Get-WindowsFeature
# is broken on this box and every empty result below is meaningless (verdict must be UNKNOWN).
Write-Output '[3/8] windows-feature'
try {
    $all = @(Get-WindowsFeature -ErrorAction Stop)
    Write-Output ('  totalFeatures=' + $all.Count + ' installed=' + (@($all | Where-Object { $_.Installed }).Count))
} catch { Write-Output ('  UNKNOWN feature-enum: ' + (Esc $_.Exception.Message)) }
$featNames = @('Web-Mgmt-Console','RSAT-AD-PowerShell','Server-Gui-Shell','Server-Gui-Mgmt-Infra','Server-Gui-Shell-Package','Desktop-Experience','Server-Media-Foundation','ServerCore-FullServer','ServerCore-Drivers-General')
# Only a SUCCESSFUL resolution counts toward $guiResolved. A throw means ServerManager does not
# recognise the name at all -- on Server 2016+ that is precisely the expected shape of "the
# feature was removed from the component store", so counting a throw as positive would turn the
# strongest absence evidence into a false CANDIDATE-FOUND verdict.
$guiResolved = @()
foreach ($n in $featNames) {
    try {
        $f = @(Get-WindowsFeature -Name $n -ErrorAction Stop)
        if ($f.Count -eq 0) {
            Write-Output ('  feat ' + $n + ' = UNRESOLVED-EMPTY')
        } else {
            foreach ($x in $f) {
                Write-Output ('  feat ' + $x.Name + ' installState=' + $x.InstallState + ' type=' + $x.FeatureType + ' installed=' + $x.Installed)
                # InstallState=Removed: payload gone, not an in-place candidate.
                if ((InGuiWhite $x.Name $GuiFeatureNames) -and ([string]$x.InstallState -ne 'Removed')) { $guiResolved += ($x.Name + '/' + $x.InstallState) }
            }
        }
        if ($n -eq 'Web-Mgmt-Console') { $evCtrl = $(if ($f.Count -gt 0) { 'ok' } else { 'err:control-unresolved-empty' }) }
    } catch {
        $msg = Esc (Clip $_.Exception.Message 150)
        Write-Output ('  feat ' + $n + ' = THROWS (name unknown to ServerManager): ' + $msg)
        if ($n -eq 'Web-Mgmt-Console') { $evCtrl = 'err:control-threw' }
    }
}
if ($guiResolved.Count -gt 0) { $ev3 = 'pos:' + ($guiResolved -join ';') } else { $ev3 = 'neg' }

# ---- [4/8] DISM optional features: is any shell/desktop component present-but-disabled,
# i.e. installable from the image payload without a re-image?
Write-Output '[4/8] optional-features'
try {
    $of = @(Get-WindowsOptionalFeature -Online -ErrorAction Stop)
    Write-Output ('  optionalFeatureTotal=' + $of.Count)
    $st = $of | Group-Object State | Sort-Object Count -Descending
    foreach ($g in $st) { Write-Output ('  state.' + $g.Name + '=' + $g.Count) }
    $hits = @($of | Where-Object { InGuiWhite $_.FeatureName $GuiFeatureNames })
    Write-Output ('  guiWhiteHits=' + $hits.Count + ' cap=' + $CapFeat)
    # Candidate = payload still on box (Enabled or Disabled). PayloadRemoved stays negative.
    $resolved = @()
    $i = 0
    foreach ($h in $hits) {
        $s = [string]$h.State
        if ($s -eq 'Enabled' -or $s -eq 'Disabled') { $resolved += ($h.FeatureName + '/' + $s) }
        if ($i -ge $CapFeat) { Write-Output '    ...truncated-by-cap'; break }
        Write-Output ('    ' + $h.FeatureName + ' state=' + $s)
        $i++
    }
    if ($resolved.Count -gt 0) { $ev4 = 'pos:' + ($resolved -join ';') } else { $ev4 = 'neg' }
} catch { Write-Output ('  UNKNOWN optional-features: ' + (Esc $_.Exception.Message)); $ev4 = 'err:' + (Esc (Clip $_.Exception.Message 120)) }

# ---- [5/8] shell binaries: what an interactive session could even launch
Write-Output '[5/8] shell-binaries'
$bins = @(
    'C:\Windows\explorer.exe','C:\Windows\System32\mmc.exe','C:\Windows\System32\shell32.dll',
    'C:\Windows\System32\twinui.pcshell.dll','C:\Windows\System32\dwm.exe','C:\Windows\System32\logonui.exe',
    'C:\Windows\System32\winlogon.exe','C:\Windows\System32\userinit.exe','C:\Windows\System32\rdpshell.exe',
    'C:\Windows\System32\rdpinit.exe','C:\Windows\System32\sconfig.exe','C:\Windows\System32\control.exe',
    'C:\Windows\System32\taskmgr.exe','C:\Windows\System32\notepad.exe','C:\Windows\System32\mspaint.exe',
    'C:\Windows\System32\mstsc.exe','C:\Windows\System32\PresentationFramework.dll',
    'C:\Windows\System32\WindowsFormsIntegration.dll','C:\Windows\System32\System.Windows.Forms.dll',
    'C:\Windows\System32\WindowsPowerShell\v1.0\powershell_ise.exe','C:\Windows\System32\ServerManager.exe',
    'C:\Windows\System32\msiexec.exe','C:\Windows\System32\regedit.exe','C:\Windows\System32\cmd.exe',
    'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','C:\Windows\System32\ntdll.dll'
)
$present = 0; $absent = 0; $i = 0
foreach ($b in $bins) {
    if ($i -ge $CapBin) { Write-Output '  ...truncated-by-cap'; break }
    $ok = $false
    try { $ok = Test-Path -LiteralPath $b } catch { $ok = $false }
    if ($ok) { $present++ } else { $absent++ }
    Write-Output ('  ' + $(if ($ok) { 'PRESENT ' } else { 'ABSENT  ' }) + $b)
    $i++
}
Write-Output ('  presentCount=' + $present + ' absentCount=' + $absent)

# ---- [6/8] what the RDP session actually launches, and which GUI-ish PowerShell surface exists
Write-Output '[6/8] interactive-shell-config'
foreach ($p in @(
        @('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon','Shell'),
        @('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon','Userinit'),
        @('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon','System'),
        @('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon','AutoRestartShell'),
        @('HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp','UserShell'),
        @('HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp','fPromptForPassword')
    )) {
    try {
        $it = Get-ItemProperty -LiteralPath $p[0] -Name $p[1] -ErrorAction Stop
        Write-Output ('  ' + $p[1] + '=' + (Esc ([string]$it.($p[1]))))
    } catch { Write-Output ('  ' + $p[1] + '=ABSENT-or-unknown (' + (Esc (Clip $_.Exception.Message 80)) + ')') }
}
foreach ($c in @('Out-GridView','Show-Command','Show-ControlPanelItem','Get-ComputerInfo','sconfig')) {
    try {
        $g = @(Get-Command $c -ErrorAction Stop)
        if ($g.Count -gt 0) { Write-Output ('  cmd ' + $c + ' = FOUND type=' + $g[0].CommandType + ' module=' + $g[0].ModuleName) }
        else { Write-Output ('  cmd ' + $c + ' = NOT-FOUND') }
    } catch { Write-Output ('  cmd ' + $c + ' = NOT-FOUND') }
}
foreach ($a in @('System.Windows.Forms','PresentationFramework','PresentationCore','Microsoft.PowerShell.GraphicalHost')) {
    try {
        Add-Type -AssemblyName $a -ErrorAction Stop
        Write-Output ('  asm ' + $a + ' = LOADED')
    } catch { Write-Output ('  asm ' + $a + ' = FAILED: ' + (Esc (Clip $_.Exception.Message 110))) }
}
try {
    Write-Output ('  psVersion=' + $PSVersionTable.PSVersion.ToString() + ' edition=' + $PSVersionTable.PSEdition + ' clr=' + $PSVersionTable.CLRVersion)
} catch { Write-Output ('  UNKNOWN psVersion: ' + (Esc $_.Exception.Message)) }

# ---- [7/8] capability packages: is a shell/desktop offered through the FoD/capability channel?
Write-Output '[7/8] windows-capability'
try {
    $capAll = @(Get-WindowsCapability -Online -ErrorAction Stop)
    Write-Output ('  capabilityTotal=' + $capAll.Count)
    $cst = $capAll | Group-Object State | Sort-Object Count -Descending
    foreach ($g in $cst) { Write-Output ('  capState.' + $g.Name + '=' + $g.Count) }
    $cgui = @($capAll | Where-Object { InGuiWhite $_.Name $GuiFeatureNames })
    Write-Output ('  capGuiWhiteHits=' + $cgui.Count + ' cap=' + $CapCap)
    $ci = 0
    foreach ($h in $cgui) {
        if ($ci -ge $CapCap) { Write-Output '    ...truncated-by-cap'; break }
        Write-Output ('    ' + $h.Name + ' state=' + $h.State)
        $ci++
    }
    $rsat = @($capAll | Where-Object { $_.Name -like 'Rsat.*' })
    Write-Output ('  rsatTotal=' + $rsat.Count + ' rsatInstalled=' + (@($rsat | Where-Object { $_.State -eq 'Installed' }).Count))
    if ($cgui.Count -gt 0) { $ev7 = 'pos:' + (($cgui | Select-Object -First 4 | ForEach-Object { $_.Name + '/' + $_.State }) -join ';') } else { $ev7 = 'neg' }
} catch { Write-Output ('  UNKNOWN windows-capability: ' + (Esc $_.Exception.Message)); $ev7 = 'err:' + (Esc (Clip $_.Exception.Message 120)) }

# ---- [8/8] verdict + no-write proof
Write-Output '[8/8] verdict-and-nowrite-post'
Write-Output ('  ev2.ServerLevels=' + $ev2)
Write-Output ('  ev3.ServerManager=' + $ev3)
Write-Output ('  ev4.OptionalFeature=' + $ev4)
Write-Output ('  ev7.Capability=' + $ev7)
Write-Output ('  evCtrl.PositiveControl(Web-Mgmt-Console)=' + $evCtrl)
$flags = @($ev2, $ev3, $ev4, $ev7)
$anyPos = @($flags | Where-Object { $_ -like 'pos:*' })
$anyErr = @($flags | Where-Object { $_ -like 'err:*' })
$ctrlBad = ($evCtrl -ne 'ok')
if ($anyPos.Count -gt 0) {
    Write-Output '  VERDICT=CANDIDATE-FOUND  an in-place GUI component resolved; see the pos: lines above'
} elseif ($anyErr.Count -gt 0 -or $ctrlBad) {
    Write-Output '  VERDICT=UNKNOWN  fail-closed: a decisive check errored or the positive control failed,'
    Write-Output '                   so an empty result cannot be distinguished from a broken cmdlet'
} else {
    Write-Output '  VERDICT=INPLACE-GUI-NOT-INSTALLABLE  all four decisive checks returned a definite negative;'
    Write-Output '           no shell/desktop component exists in the installed image or in the capability channel,'
    Write-Output '           which matches the Server 2016+ removal of in-place Server Core -> Desktop conversion'
}
$postSelf = StampOf $Self
$postOs   = StampOf $OsProxy
Write-Output ('  selfStamp.post=' + $postSelf + ' unchanged=' + ($postSelf -eq $preSelf))
Write-Output ('  osProxyStamp.post=' + $postOs + ' unchanged=' + ($postOs -eq $preOs))
Write-Output ('  nowriteAssert=' + $(($postSelf -eq $preSelf) -and ($postOs -eq $preOs)))
Write-Output ('sentinel=' + $Sentinel)
