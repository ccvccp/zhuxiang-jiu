# trae_pub3xzx.ps1 - t27a: surgical in-place publish of 3 source-of-truth pages
# to zxjiu.com (39.107.43.149), site root C:\www\dist
#
# WHY THE $SPECS TABLE IS REUSED VERBATIM FROM t23: the t24 read-only service
# probe measured these three pages on THIS host (section [6/9] CONTENT ROOTS)
# and got byte counts + sha16 IDENTICAL to the pre-patch baseline that t23 saw
# on 8.147.66.237. Both hosts therefore carry the same old batch of files, so
# the same pre/post expectations apply. Only the wire target changed:
#   Send -RegionId cn-beijing -InstanceId i-2zebz5dnx9903xdsbi7m
# (InvokeId prefix t-bj07... = cn-beijing; t-wl07... would be the wrong host.)
# NOTE: nginx is currently Paused on this host, so these pages are NOT reachable
# from the internet right now. Patching first is a hard ordering constraint --
# restoring the service before this step would put the high-risk quickFill block
# (plaintext phone + plaintext password) live on zxjiu.com.
#
# WHY IN-PLACE PATCHING, NOT FILE TRANSFER: the three files total 300045 bytes,
# far past the cloud-assistant 24576-char base64 wire cap. The round-4 probe
# (probe4b) proved the online-vs-source delta is a PURE SUBTRACTION:
#   activity.html        L254   placeholder="13800000001" -> 6 Chinese chars
#   activity-detail.html L311   same
#   xiaozhu-voice.html   L349+  5 extra lines (quickFill button + fn block)
# so we patch in place and gate on FULL sha256 equality against the hashes of
# the local source of truth. sha256 equality is the ONLY arbiter; byte deltas
# are corroborating evidence only (hand arithmetic already lied once: I counted
# 258 bytes for the 5-line block, the true delta is 256).
# Any mismatch => restore from the verified backup and exit 1 (fail-closed,
# security-hard-coded 2.3 / 2.4).
#
# This script is 100% ASCII. The Chinese placeholder is assembled from
# codepoints MEASURED on the source files by cpcheck.ps1 (local, read-only):
#   0x8BF7,0x8F93,0x5165,0x624B,0x673A,0x53F7   (6 chars / 18 UTF-8 bytes)
# Byte arithmetic now closes: -11 +18 = +7 per placeholder page
#   30354+7=30361   43030+7=43037   226903-256=226647
#
# Target paths are a HARDCODED WHITELIST. Nothing is parameterized, so there is
# no path-traversal surface (security-hard-coded 1.8).

$ErrorActionPreference = 'Stop'

$DIST  = 'C:\www\dist'
$STAMP = Get-Date -Format 'yyyyMMddHHmmss'

function Note([string]$s) { Write-Output $s }

$sha = [System.Security.Cryptography.SHA256]::Create()
function Get-Sha256([byte[]]$b) {
    return (([BitConverter]::ToString($sha.ComputeHash($b))) -replace '-', '')
}
function Count-Lines([string]$t) { return ($t.Split("`n")).Count }

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 6 Chinese chars of the phone placeholder, built from measured codepoints
$PH = -join ([char]0x8BF7, [char]0x8F93, [char]0x5165, [char]0x624B, [char]0x673A, [char]0x53F7)

$SPECS = @(
    @{ Name = 'activity.html'
       PreSha16 = '08E594F46E8E5556'; PreBytes = 30354
       PostSha256 = 'CFB0D6634C58F2DDA6467BE07DE17406122CBAF070D7362CC254A52BBBD5F0E1'
       PostBytes = 30361; Kind = 'placeholder'; Backup = ''; BackupOk = $false },
    @{ Name = 'activity-detail.html'
       PreSha16 = '0DA59F6ED07A7C2F'; PreBytes = 43030
       PostSha256 = '3A114C112CE190D0FFEE25CF9C9F1C9F13F7CD9F1384393F6246A922D0483814'
       PostBytes = 43037; Kind = 'placeholder'; Backup = ''; BackupOk = $false },
    @{ Name = 'xiaozhu-voice.html'
       PreSha16 = '53FFA0C414BF033B'; PreBytes = 226903
       PostSha256 = '8ABC34674A6AA67AD5B0E3F41AC4AE99BD2463CD18EE690B2E77F655BF9BADB6'
       PostBytes = 226647; Kind = 'delete5'; Backup = ''; BackupOk = $false }
)

Note ('=== PUB3XZX t27a | siteRoot=' + $DIST + ' | stamp=' + $STAMP + ' | placeholderChars=' + $PH.Length + ' placeholderUtf8Bytes=' + [Text.Encoding]::UTF8.GetByteCount($PH))
Note ''
Note '=== [1/4] PREFLIGHT (read-only, no write) ==='
$preflightOk = $true
foreach ($s in $SPECS) {
    $p = Join-Path $DIST $s.Name
    if (-not (Test-Path -LiteralPath $p)) {
        Note ('  MISSING | ' + $s.Name + ' at ' + $p)
        $preflightOk = $false
        continue
    }
    $b  = [IO.File]::ReadAllBytes($p)
    $h  = Get-Sha256 $b
    $h16 = $h.Substring(0, 16)
    $t  = [Text.Encoding]::UTF8.GetString($b)
    $lf   = ([regex]::Matches($t, "`n")).Count
    $crlf = ([regex]::Matches($t, "`r`n")).Count
    $qf   = ([regex]::Matches($t, 'quickFill')).Count
    $spm  = ([regex]::Matches($t, [regex]::Escape('13800000001'))).Count
    $sd   = ([regex]::Matches($t, 'test123456')).Count
    $stateOk = ($h16 -eq $s.PreSha16) -and ($b.Length -eq $s.PreBytes)
    if (-not $stateOk) { $preflightOk = $false }
    Note ('  ' + $s.Name + ' | bytes=' + $b.Length + '/' + $s.PreBytes + ' | sha16=' + $h16 + '/' + $s.PreSha16 + ' | stateMatch=' + $stateOk)
    Note ('      lf=' + $lf + ' crlf=' + $crlf + ' lines=' + (Count-Lines $t) + ' | quickFill=' + $qf + ' seedPhone=' + $spm + ' seedPwd=' + $sd)
}
if (-not $preflightOk) {
    Note ''
    Note 'VERDICT-PREFLIGHT FAIL: online state does not match the round-4 probe baseline.'
    Note 'Refusing to patch anything. NO file was read-modified, NO backup was created.'
    Note '=== PUB3XZX-DONE ==='
    exit 1
}
Note 'VERDICT-PREFLIGHT PASS: all three files match the probe baseline byte-for-byte.'
# --- CHUNK-A-END ---

Note ''
Note '=== [2/4] BACKUP (must precede any write; security-hard-coded 2.4) ==='
foreach ($s in $SPECS) {
    $src = Join-Path $DIST $s.Name
    $bak = $src + '.bak-' + $STAMP
    Copy-Item -LiteralPath $src -Destination $bak -Force
    $sb = [IO.File]::ReadAllBytes($src)
    $bb = [IO.File]::ReadAllBytes($bak)
    $eq = ((Get-Sha256 $sb) -eq (Get-Sha256 $bb))
    $s.Backup = $bak
    $s.BackupOk = $eq
    Note ('  ' + $s.Name + ' | backupPath=' + $bak + ' | backupBytes=' + $bb.Length + ' | hashEqual=' + $eq)
}
$bkAllOk = $true
foreach ($s in $SPECS) { if (-not $s.BackupOk) { $bkAllOk = $false } }
if (-not $bkAllOk) {
    Note ''
    Note 'VERDICT-BACKUP FAIL: a backup did not hash-equal its source, so rollback would be'
    Note 'impossible. NO file has been patched. Re-run after the disk/permission issue clears.'
    Note '=== PUB3XZX-DONE ==='
    exit 1
}
Note 'VERDICT-BACKUP PASS: 3 hash-verified backups in place.'

Note ''
Note '=== [3/4] PATCH + FULL sha256 GATE (rollback from verified backup on any failure) ==='

# ASCII-only operators. $NEWPH was validated against the local source of truth, which
# reads exactly: placeholder="<CJK: U+8BF7 U+8F93 U+5165 U+624B U+673A U+53F7>" -- 6 chars /
# 18 UTF-8 bytes, in DOUBLE quotes, at L254 / L311. Each file also carries a second,
# untouched placeholder two lines below (the login-password one), so the occurrence
# guard below must see exactly 1 hit, never 2.
# The deletion regexes use [^\n]* so the Chinese text and the plaintext credential
# inside the block are swallowed without ever being spelled out in this script.
# $REFN is deliberately LAZY up to the first line-initial closing brace, so it stays
# correct whether the function body is 1, 2 or 3 lines. Over- or under-deletion cannot
# slip through: the full-sha256 gate below is the only thing that authorizes a write.
$OLDPH = 'placeholder="13800000001"'
$NEWPH = 'placeholder="' + $PH + '"'
$REBTN = '(?m)^[^\n]*onclick="quickFill\(\)"[^\n]*\r?\n'
$REFN  = '(?m)^function quickFill\(\) \{\r?\n(?:[^\n]*\r?\n)*?\}\r?\n'

$patchFail = $false
foreach ($s in $SPECS) {
    $path = Join-Path $DIST $s.Name
    $s.Result = 'NOT-ATTEMPTED'
    Note ('  --- ' + $s.Name + ' ---')
    try {
        $b0 = [IO.File]::ReadAllBytes($path)
        $t0 = [Text.Encoding]::UTF8.GetString($b0)
        $n0 = Count-Lines $t0

        if ($s.Kind -eq 'delete5') {
            # Content-free structural dump of the quickFill window. It never prints the
            # block text, because that text is the plaintext credential we are removing
            # (security-hard-coded 1.2 / 2.1: no secrets in logs or reports). Shape +
            # per-line sha8 is enough to diagnose a failed gate on the next round.
            $lines = $t0.Split("`n")
            $idx = @()
            for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match 'quickFill') { $idx += $i } }
            if ($idx.Count -gt 0) {
                $from = $idx[0]
                $to = [Math]::Min($lines.Count - 1, $idx[$idx.Count - 1] + 3)
                Note ('      quickFill window L' + ($from + 1) + '..L' + ($to + 1) + ' (structure only, no content):')
                for ($i = $from; $i -le $to; $i++) {
                    $lt = $lines[$i].TrimEnd("`r").Trim()
                    Note ('        L' + ($i + 1) + ' chars=' + $lines[$i].Length + ' trimmedLen=' + $lt.Length + ' endsBrace=' + ($lt -eq '}') + ' hasQuickFill=' + ($lines[$i] -match 'quickFill') + ' sha8=' + (Get-Sha256 $utf8NoBom.GetBytes($lines[$i])).Substring(0, 8))
                }
            } else {
                Note '      quickFill window: NOT FOUND (nothing to delete?)'
            }
        }

        if ($s.Kind -eq 'placeholder') {
            $cnt = ([regex]::Matches($t0, [regex]::Escape($OLDPH))).Count
            if ($cnt -ne 1) { throw ('occurrence guard: placeholder="13800000001" hits=' + $cnt + ', expected exactly 1') }
            $t1 = $t0.Replace($OLDPH, $NEWPH)
        } else {
            $c1 = ([regex]::Matches($t0, $REBTN)).Count
            $c2 = ([regex]::Matches($t0, $REFN)).Count
            if ($c1 -ne 1) { throw ('occurrence guard: quickFill button line hits=' + $c1 + ', expected exactly 1') }
            if ($c2 -ne 1) { throw ('occurrence guard: quickFill function block hits=' + $c2 + ', expected exactly 1') }
            $t1 = [regex]::Replace($t0, $REBTN, '')
            $t1 = [regex]::Replace($t1, $REFN, '')
        }

        $n1 = Count-Lines $t1
        $b1 = $utf8NoBom.GetBytes($t1)
        $h1 = Get-Sha256 $b1
        # delta is signed "after minus before", so a deletion prints negative and a
        # growth prints positive (t23 printed them inverted: +7 showed up as -7).
        $dLines = $n1 - $n0
        $dBytes = $b1.Length - $b0.Length
        Note ('      edited bytes=' + $b0.Length + ' -> ' + $b1.Length + ' (delta ' + $dBytes + ') | lines=' + $n0 + ' -> ' + $n1 + ' (delta ' + $dLines + ') | sha16=' + $h1.Substring(0, 16))

        # Leak scan on the patched in-memory text: all three literals must be gone.
        $qf  = ([regex]::Matches($t1, 'quickFill')).Count
        $spm = ([regex]::Matches($t1, [regex]::Escape('13800000001'))).Count
        $sd  = ([regex]::Matches($t1, 'test123456')).Count
        Note ('      postPatchLeakScan quickFill=' + $qf + ' seedPhone=' + $spm + ' seedPwd=' + $sd)
        if (($qf -ne 0) -or ($spm -ne 0) -or ($sd -ne 0)) { throw 'leak scan: patched text still contains a leak literal' }

        # THE GATE. Full sha256 equality with the local source of truth is the only
        # arbiter. Byte/line deltas are logged as corroborating evidence, never as proof.
        if ($h1 -ne $s.PostSha256) {
            throw ('sha256 GATE FAIL: patched=' + $h1 + ' expected=' + $s.PostSha256)
        }
        if ($b1.Length -ne $s.PostBytes) {
            throw ('byte GATE FAIL: patched=' + $b1.Length + ' expected=' + $s.PostBytes)
        }
        Note '      GATE PASS: full sha256 equals the local source of truth -> writing to disk'

        [IO.File]::WriteAllBytes($path, $b1)
        $b2 = [IO.File]::ReadAllBytes($path)
        $h2 = Get-Sha256 $b2
        if ($h2 -ne $s.PostSha256) { throw ('post-write re-read sha256 mismatch: ' + $h2 + ' -- rolling back') }
        $t2 = [Text.Encoding]::UTF8.GetString($b2)
        Note ('      WRITTEN OK | diskBytes=' + $b2.Length + ' | diskSha16=' + $h2.Substring(0, 16) + ' | reReadVerified=True | diskLf=' + ([regex]::Matches($t2, "`n")).Count + ' diskCrlf=' + ([regex]::Matches($t2, "`r`n")).Count)
        $s.Result = 'PATCHED'
    } catch {
        Note ('      ERROR: ' + $_.Exception.Message)
        $s.Result = 'FAILED'
        $patchFail = $true
        if ($s.Backup -and (Test-Path -LiteralPath $s.Backup)) {
            Note ('      rolling back from verified backup: ' + $s.Backup)
            try {
                Copy-Item -LiteralPath $s.Backup -Destination $path -Force
                $rb = [IO.File]::ReadAllBytes($path)
                $rh = (Get-Sha256 $rb).Substring(0, 16)
                if ($rh -ne $s.PreSha16) {
                    Note ('      ROLLBACK-BROKEN: disk sha16=' + $rh + ' but pre-patch was ' + $s.PreSha16 + ' -- MANUAL RESTORE NEEDED from ' + $s.Backup)
                    $s.Result = 'FAILED-ROLLBACK-BROKEN'
                } else {
                    Note ('      rolled back OK | diskBytes=' + $rb.Length + ' | sha16=' + $rh + ' == pre-patch baseline')
                    $s.Result = 'FAILED-ROLLEDBACK'
                }
            } catch {
                Note ('      ROLLBACK-ERROR: ' + $_.Exception.Message + ' -- restore manually from ' + $s.Backup)
                $s.Result = 'FAILED-ROLLBACK-ERROR'
            }
        } else {
            Note '      NO VERIFIED BACKUP AVAILABLE -- nothing was written, file left untouched.'
        }
    }
}
# --- CHUNK-B-END ---

Note ''
Note '=== [4/4] SUMMARY ==='
foreach ($s in $SPECS) {
    $p = Join-Path $DIST $s.Name
    $fb = [IO.File]::ReadAllBytes($p)
    $fh = Get-Sha256 $fb
    $eqPost = ($fh -eq $s.PostSha256)
    $eqPre  = ($fh.Substring(0, 16) -eq $s.PreSha16)
    Note ('  ' + $s.Name + ' | result=' + $s.Result + ' | diskBytes=' + $fb.Length + ' | sha16=' + $fh.Substring(0, 16) + ' | matchesSourceOfTruth=' + $eqPost + ' | matchesPrePatch=' + $eqPre + ' | backup=' + $s.Backup)
}
if ($patchFail) {
    Note ''
    Note 'VERDICT-PUBLISH FAIL: at least one file did not reach the source-of-truth hash. It was rolled back to its pre-patch state; nothing is left half-patched.'
    Note '=== PUB3XZX-DONE ==='
    exit 1
}
Note ''
Note 'VERDICT-PUBLISH PASS: 3/3 files are now byte-identical (full sha256) to the local source of truth. Removed: high-risk quickFill block (plaintext phone + plaintext password) and mid-risk seed-phone placeholders.'
Note '=== PUB3XZX-DONE ==='
exit 0
