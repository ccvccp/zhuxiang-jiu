# trae_mknginxzxwire2.ps1 -- generator for a TWO-PART gzip+base64 wire wrapper.
#
# Why two parts. The cloud assistant CommandContent cap is 24576 base64 chars, i.e. exactly
# 18432 raw ASCII bytes (24576/4*3 = 18432; 18433 bytes would encode to 24580 and be rejected).
# The t27c inner script trae_nginxzx.ps1 is 44470 bytes; gzip -Optimal gives 14432 bytes and its
# base64 blob is 19244 chars. 19244 > 18432, so THE BLOB ALONE does not fit in one command --
# before any wrapper code is added. Same fact from the other side: re-encoding those 19244 base64
# chars as UTF-8 and base64-ing again gives ~25659, already ~1100 over the cap with a zero-byte
# wrapper. The single-part generator trae_mkdedupwire.ps1 was run on this inner and measured
# wrapper 21195 bytes / b64WireLen 28260 / fits=False, then threw at its own final gate.
#
# Shrinking the inner is not an option: it writes production service and site state, and its
# sentinel/exit counts are pinned 9:9 at exact line numbers by trae_gate_nginxzx.ps1. Rewriting it
# would invalidate that calibration for no gain.
#
# So the blob is split across two commands:
#   part 1 (trae_nginxzx_wire_p1.ps1) carries chunk A ONLY. It runs no inner script, touches no
#     production path, changes no service or site. Its single write is its own part file under
#     $env:TEMP, which it then reads back and sha256-verifies before printing WIRE-P1-DONE.
#   part 2 (trae_nginxzx_wire.ps1) carries chunk B, reads the part file back, re-verifies its
#     length and sha256, concatenates, and only then decodes/decompresses/verifies/runs the inner.
# Both run as SYSTEM under the cloud assistant, so $env:TEMP is C:\Windows\TEMP in both and part 2
# always finds what part 1 left.
#
# Integrity argument: part 2 verifies sha256 AND length of the DECOMPRESSED inner. That check is a
# function of both chunks, so a stale, truncated or corrupted part1 cannot possibly produce the
# correct inner bytes. Part 1 therefore needs no separate integrity proof; its own re-read print
# exists for early failure and human comparison, not as the security boundary.
#
# ORDER IS DELIBERATE. Every check that can run in memory (ASCII purity, wire cap, Parser, the
# gzip round-trip self-test AND the split-reassembly self-test) runs BEFORE any file is written.
# The single-part generator wrote at its line 139 and threw at line 157, which left a 21195-byte
# residual wrapper on disk that had to be found and deleted by hand. If a post-write re-read then
# disagrees, the catch block deletes BOTH outputs before rethrowing, so no half-pair survives.
#
# The inner script is not modified in any way.
#
# Note on the templates: they deliberately contain NO nested here-string. A line starting with '@
# would terminate the outer here-string early, so each blob is emitted as chunked single-quoted
# string assignments (the base64 alphabet contains no quote, no $ and no backtick).
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Src,
    [Parameter(Mandatory = $true, Position = 1)][string]$OutP1,
    [Parameter(Mandatory = $true, Position = 2)][string]$OutP2,
    [int]$SplitAt = 12000
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$CAP = 24576

function Hex([byte[]]$b) {
    return (([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))) -replace '-', '')
}
# @($null).Count is 1, not 0 -- the Parser's [ref] stays $null when a file is clean, so every count
# of it must go through this. Counting it inline produced a phantom parse error once already.
function Cnt($v) {
    if ($null -eq $v) { return 0 }
    return @($v).Count
}
# return , $out (the comma operator) keeps the result a byte[]. Callers MUST use a bare assignment:
# @(Gunzip ...) double-wraps it into a 1-element array of byte[] -- the same trap class as
# @(Get-PortListeners 80) in the inner script.
function Gunzip([byte[]]$cmp) {
    $msIn = New-Object IO.MemoryStream(, $cmp)
    $ds = New-Object IO.Compression.GZipStream($msIn, [IO.Compression.CompressionMode]::Decompress)
    $msR = New-Object IO.MemoryStream
    $ds.CopyTo($msR)
    $ds.Close(); $msIn.Close()
    $out = $msR.ToArray()
    $msR.Close()
    return , $out
}
function Norm([string]$s) {
    $s = $s -replace "`r`n", "`n"
    return ($s -replace "`n", "`r`n")
}
# Emits  $<var> = ''  followed by one  $<var> += '<piece>'  line per chunk.
# Quote arithmetic, spelled out because it is unreadable at a glance:
#   '$A += ''' tokenizes as  ' open | $A +=  literal | '' escaped to one ' | ' close  =>  $A += '
#   ''''       tokenizes as  ' open | '' escaped to one ' | ' close                  =>  '
# so the concatenations below yield exactly  $A = ''  and  $A += '<piece>'.
# The size parameter is $size, not $ChunkSize: PowerShell names are case-insensitive and a
# per-iteration payload string would have clobbered it (that exact collision, $Chunk vs $chunk,
# already threw InvalidCastFromStringToInteger in the single-part generator).
function BlobBlock([string]$varName, [string]$payload, [int]$size) {
    $sb = New-Object Text.StringBuilder
    [void]$sb.AppendLine('$' + $varName + ' = ''''')
    for ($i = 0; $i -lt $payload.Length; $i += $size) {
        $piece = $payload.Substring($i, [Math]::Min($size, $payload.Length - $i))
        [void]$sb.AppendLine('$' + $varName + ' += ''' + $piece + '''')
    }
    return $sb.ToString().TrimEnd("`r", "`n")
}

if (-not (Test-Path -LiteralPath $Src)) { throw ('missing ' + $Src) }
$bytes = [IO.File]::ReadAllBytes($Src)
$innerSha = Hex $bytes
$innerName = [IO.Path]::GetFileName($Src)
$na = 0
foreach ($b in $bytes) { if ($b -gt 127) { $na++ } }
$bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
Write-Host ('inner name=' + $innerName + ' bytes=' + $bytes.Length + ' bom=' + $bom + ' nonAscii=' + $na)
Write-Host ('inner sha256=' + $innerSha)
if ($na -ne 0) {
    # A BOM'd non-ascii inner would need a WriteAllBytes + explicit BOM-strip decision; refuse
    # rather than silently ship something whose encoding we did not reason about.
    throw ('inner script has ' + $na + ' non-ascii bytes -- this generator only handles pure ASCII payloads')
}

$msC = New-Object IO.MemoryStream
$gz = New-Object IO.Compression.GZipStream($msC, [IO.Compression.CompressionLevel]::Optimal, $true)
$gz.Write($bytes, 0, $bytes.Length)
$gz.Close()
$comp = $msC.ToArray()
$msC.Close()
$b64 = [Convert]::ToBase64String($comp)
Write-Host ('gzip bytes=' + $comp.Length + ' b64 chars=' + $b64.Length + ' ratio=' + [math]::Round($comp.Length / $bytes.Length, 3))
Write-Host ('rawCap=18432 blobAloneFitsOneCommand=' + ($b64.Length -le 18432) + ' (False is the entire reason this generator exists)')

# self-test 1: the single-blob gzip round-trip is byte-exact.
$back = Gunzip ([Convert]::FromBase64String($b64))
if ($back.Length -ne $bytes.Length) { throw ('generator self-test: length ' + $back.Length + ' != ' + $bytes.Length) }
if ((Hex $back) -cne $innerSha) { throw 'generator self-test: gzip round-trip is not byte-exact' }

if ($SplitAt -le 0 -or $SplitAt -ge $b64.Length) { throw ('SplitAt ' + $SplitAt + ' is outside 1..' + ($b64.Length - 1)) }
$chunkA = $b64.Substring(0, $SplitAt)
$chunkB = $b64.Substring($SplitAt)
if (($chunkA.Length + $chunkB.Length) -ne $b64.Length) { throw 'split lost characters' }
if (($chunkA + $chunkB) -cne $b64) { throw 'split is not byte-exact' }
foreach ($cx in @($chunkA, $chunkB)) {
    foreach ($ch in $cx.ToCharArray()) { if ([int]$ch -gt 127) { throw 'blob chunk contains a non-ascii character' } }
}
$partName = 'trae_nginxzx.b64part1'
$partSha = Hex ([Text.Encoding]::UTF8.GetBytes($chunkA))
Write-Host ('chunkA chars=' + $chunkA.Length + ' chunkB chars=' + $chunkB.Length + ' part1 sha256=' + $partSha)

# self-test 2: the SPLIT path round-trips, not just the single-blob path. Going through
# UTF8.GetBytes/GetString reproduces exactly what part 1 writes and part 2 reads back, so this
# proves the reassembly arithmetic -- including the part-file encoding hop -- is byte-exact.
$simA = [Text.Encoding]::UTF8.GetString([Text.Encoding]::UTF8.GetBytes($chunkA))
$back2 = Gunzip ([Convert]::FromBase64String($simA + $chunkB))
if ($back2.Length -ne $bytes.Length) { throw ('split self-test: length ' + $back2.Length + ' != ' + $bytes.Length) }
if ((Hex $back2) -cne $innerSha) { throw 'split self-test: reassembled blob does not decode to the exact inner bytes' }
Write-Host 'generatorSelfTest=OK (gzip round-trip AND split-reassembly round-trip both byte-exact)'

# Chunk size 8000 is a size lever, not a style choice: every chunk costs 12 bytes of PS syntax.
$ChunkSize = 8000
$blobA = BlobBlock 'A' $chunkA $ChunkSize
$blobB = BlobBlock 'B' $chunkB $ChunkSize

$tpl1 = @'
# AUTO-GENERATED wire part 1 of 2 -- regenerate with trae_mknginxzxwire2.ps1, do not edit.
# inner __INNER_NAME__ bytes __INNER_LEN__ sha256 __INNER_SHA__
# This part carries blob chunk A (__CHUNA__ of __NB64__ base64 chars) and NOTHING ELSE. It runs no
# inner script, touches no production path, and makes no service or site change. Its only write is
# its own part file under $env:TEMP, which part 2 (trae_nginxzx_wire.ps1) reads back and verifies.
# Why the split: the whole blob is __NB64__ chars, over the 18432-raw-byte single-command cap.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$PartName = '__PARTNAME__'
$PartLen  = __CHUNA__
$PartSha  = '__PARTSHA__'

__BLOBA__

function Hex([byte[]]$b) {
    return (([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))) -replace '-', '')
}

if ($A.Length -ne $PartLen) { throw ('chunk A in this file is ' + $A.Length + ' chars, expected ' + $PartLen + ' -- refusing to drop a truncated blob') }
$partPath = Join-Path $env:TEMP $PartName
# UTF8, not ASCII: ASCII.GetBytes silently maps every non-ascii char to '?', which would let a
# corrupted blob through as plausible-looking base64. UTF8 keeps >127 as real bytes so the
# length/sha256 re-read below can catch it.
$partBytes = [Text.Encoding]::UTF8.GetBytes($A)
# Overwrite, never Remove-Item then write: a delete-then-write window would leave no part file at
# all if this command died in between, and the wrapper verb scan forbids Remove-Item.
[IO.File]::WriteAllBytes($partPath, $partBytes)
$rd = [IO.File]::ReadAllBytes($partPath)
$sha = Hex $rd
Write-Host ('part1=' + $partPath + ' b64chars=' + $A.Length + ' bytes=' + $rd.Length + ' sha256=' + $sha)
if ($rd.Length -ne $PartLen) { throw ('on-disk part1 length ' + $rd.Length + ' != ' + $PartLen + ' -- refusing to continue') }
if ($sha -cne $PartSha) { throw ('on-disk part1 sha256 mismatch at ' + $partPath + ' -- refusing to continue') }
Write-Host 'part1Verified=True'
Write-Host 'WIRE-P1-DONE'
'@

$tpl2 = @'
# AUTO-GENERATED wire wrapper part 2 of 2 -- regenerate with trae_mknginxzxwire2.ps1, do not edit.
# inner __INNER_NAME__ bytes __INNER_LEN__ sha256 __INNER_SHA__
# gzipped: the inner exceeds the 18432-byte raw cap, and its base64 blob (__NB64__ chars) exceeds
# that cap on its own, so the blob is split across two commands. Chunk A (__CHUNA__ chars) was
# dropped into TEMP by part 1; chunk B (__CHUNB__ chars) is below. The inner is rebuilt byte-exactly
# and sha256+length are verified twice -- once in memory, once after writing to disk -- before a
# single line of it runs. That check covers BOTH chunks, so part 1 needs no separate proof.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$InnerName = '__INNER_NAME__'
$InnerLen  = __INNER_LEN__
$InnerSha  = '__INNER_SHA__'
$PartName  = '__PARTNAME__'
$PartLen   = __CHUNA__
$PartSha   = '__PARTSHA__'
$BlobLen   = __NB64__

__BLOBB__

function Hex([byte[]]$b) {
    return (([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))) -replace '-', '')
}

$partPath = Join-Path $env:TEMP $PartName
if (-not (Test-Path -LiteralPath $partPath)) { throw ('part 1 was never run on this host: missing ' + $partPath + ' -- refusing to run anything') }
$partBytes = [IO.File]::ReadAllBytes($partPath)
$partShaNow = Hex $partBytes
Write-Host ('part1=' + $partPath + ' bytes=' + $partBytes.Length + ' sha256=' + $partShaNow)
if ($partBytes.Length -ne $PartLen) { throw ('stale or truncated part1: ' + $partBytes.Length + ' bytes != ' + $PartLen + ' -- refusing to run anything') }
if ($partShaNow -cne $PartSha) { throw 'stale part1: sha256 mismatch -- refusing to run anything' }
Write-Host 'part1Verified=True'

$B64 = [Text.Encoding]::UTF8.GetString($partBytes) + $B
Write-Host ('wrapper innerName=' + $InnerName + ' innerLen=' + $InnerLen + ' b64chars=' + $B64.Length + ' (chunkA=' + $PartLen + ' chunkB=' + $B.Length + ')')
if ($B64.Length -ne $BlobLen) { throw ('reassembled blob is ' + $B64.Length + ' chars, expected ' + $BlobLen + ' -- refusing to run anything') }
$cmp = [Convert]::FromBase64String($B64)
$msIn = New-Object IO.MemoryStream(,$cmp)
$ds = New-Object IO.Compression.GZipStream($msIn, [IO.Compression.CompressionMode]::Decompress)
$msOut = New-Object IO.MemoryStream
$ds.CopyTo($msOut)
$ds.Close()
$msIn.Close()
$payload = $msOut.ToArray()
$msOut.Close()

$sha = Hex $payload
Write-Host ('unpacked bytes=' + $payload.Length + ' sha256=' + $sha)
if ($payload.Length -ne $InnerLen) { throw ('decompressed length ' + $payload.Length + ' != ' + $InnerLen + ' -- refusing to run anything') }
if ($sha -cne $InnerSha) { throw 'decompressed sha256 mismatch -- refusing to run anything' }
Write-Host 'unpackVerified=True'

$innerPath = Join-Path $env:TEMP $InnerName
[IO.File]::WriteAllBytes($innerPath, $payload)
$disk = [IO.File]::ReadAllBytes($innerPath)
$sha2 = Hex $disk
Write-Host ('inner=' + $innerPath + ' bytes=' + $disk.Length + ' sha256=' + $sha2)
if ($sha2 -cne $InnerSha) { throw ('on-disk sha256 mismatch at ' + $innerPath + ' -- refusing to run') }
Write-Host 'diskVerified=True'

& $innerPath
Write-Host ('innerExitCode=' + $LASTEXITCODE)
Write-Host 'WIRE-DONE'
'@

function Fill([string]$tpl) {
    return $tpl.Replace('__INNER_NAME__', $innerName).Replace('__INNER_LEN__', [string]$bytes.Length).Replace('__INNER_SHA__', $innerSha).Replace('__NB64__', [string]$b64.Length).Replace('__CHUNA__', [string]$chunkA.Length).Replace('__CHUNB__', [string]$chunkB.Length).Replace('__PARTSHA__', $partSha).Replace('__PARTNAME__', $partName).Replace('__BLOBA__', $blobA).Replace('__BLOBB__', $blobB)
}
$w1 = Norm (Fill $tpl1)
$w2 = Norm (Fill $tpl2)
foreach ($w in @($w1, $w2)) {
    if ($w.Contains('__')) { throw 'an unreplaced placeholder survived Fill -- aborting before any file is written' }
}

# ---- in-memory gates: nothing is written until both parts pass all of these ----
function GateText([string]$what, [string]$text) {
    $tb = [Text.Encoding]::UTF8.GetBytes($text)
    foreach ($x in $tb) { if ($x -gt 127) { throw ($what + ' is not pure ASCII -- aborting before any file is written') } }
    $wl = [Convert]::ToBase64String($tb).Length
    $te = $null; $tt = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tt, [ref]$te)
    $pc = Cnt $te
    foreach ($e in @($te)) { Write-Host ('  ' + $what + ' PARSE L' + $e.Extent.StartLineNumber + ': ' + $e.Message) }
    Write-Host ($what + ' chars=' + $text.Length + ' bytes=' + $tb.Length + ' b64WireLen=' + $wl + ' cap=' + $CAP + ' fits=' + ($wl -le $CAP) + ' headroom=' + ($CAP - $wl) + ' parseErrors=' + $pc)
    if ($pc -ne 0) { throw ($what + ' does not parse -- aborting before any file is written') }
    if ($wl -gt $CAP) { throw ($what + ' exceeds the wire cap -- aborting before any file is written') }
}
GateText 'p1' $w1
GateText 'p2' $w2
Write-Host 'preWriteGates=OK (both parts ASCII-pure, parse clean, under the wire cap)'

[IO.File]::WriteAllBytes($OutP1, [Text.Encoding]::UTF8.GetBytes($w1))
[IO.File]::WriteAllBytes($OutP2, [Text.Encoding]::UTF8.GetBytes($w2))

function Confirm([string]$path, [string]$expectText) {
    $rd = [IO.File]::ReadAllBytes($path)
    $body = [IO.File]::ReadAllText($path)
    $e = $null; $t = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$t, [ref]$e)
    $wl = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body)).Length
    Write-Host ('written path=' + $path + ' bytes=' + $rd.Length + ' sha256=' + (Hex $rd) + ' crlf=' + ([regex]::Matches($body, "`r`n")).Count + ' parseErrors=' + (Cnt $e) + ' b64WireLen=' + $wl + ' fits=' + ($wl -le $CAP))
    if ((Cnt $e) -ne 0) { throw ('post-write parse failure for ' + $path) }
    if ($wl -gt $CAP) { throw ('post-write wire cap failure for ' + $path) }
    # ReadAllText strips a BOM, so the text comparison alone would be a false green on BOM drift.
    if ($rd[0] -eq 0xEF) { throw ('post-write BOM detected in ' + $path) }
    if ($body -cne $expectText) { throw ('post-write content drift for ' + $path) }
}
try {
    Confirm $OutP1 $w1
    Confirm $OutP2 $w2
}
catch {
    # No half-pair survives: a stale part file next to a fresh wrapper is exactly the shape that
    # caused the td12d stale-blob incident.
    foreach ($p in @($OutP1, $OutP2)) { if (Test-Path -LiteralPath $p) { [IO.File]::Delete($p) } }
    Write-Host 'residualCleanup=both outputs deleted'
    throw
}
Write-Host 'MKWIRE2-DONE'
