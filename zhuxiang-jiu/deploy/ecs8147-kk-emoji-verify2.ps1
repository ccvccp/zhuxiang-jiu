# surrogate-pair emoji count (PS5-safe)
$idx = [IO.File]::ReadAllText('C:\www\dist\index.html')
$n = 0
for ($i = 0; $i -lt $idx.Length - 1; $i++) {
    $c = [int]$idx[$i]
    if ($c -ge 0xD800 -and $c -le 0xDBFF) {
        $lo = [int]$idx[$i + 1]
        if ($lo -ge 0xDC00 -and $lo -le 0xDFFF) { $n++ }
    }
}
Write-Host ('surrogate-pair(emoji) count = ' + $n)
Write-Host ('KK_ICON block present = ' + ($idx -match 'KK_ICON'))
