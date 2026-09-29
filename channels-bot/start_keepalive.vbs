' channels-bot keepalive launcher (hidden, for Windows Task Scheduler)
' Usage: wscript start_keepalive.vbs config_keepalive.json
' NOTE: keep this file ASCII-only (wscript parses non-BOM files as ANSI);
'       node paths below contain no spaces, quoting kept simple on purpose.
Dim cfg, fso, sh, dir, nodeExe
If WScript.Arguments.Count < 1 Then WScript.Quit 1
cfg = WScript.Arguments(0)

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
' relative path avoids hardcoding the CJK directory (VBS Chr() is ANSI-only 0-255)
nodeExe = dir & "\..\nodejs\node-v20.18.2-win-x64\node.exe"

If Not fso.FileExists(nodeExe) Then WScript.Quit 2
If Not fso.FileExists(dir & "\" & cfg) Then WScript.Quit 3

sh.CurrentDirectory = dir
' window style 0 = hidden; stdout/stderr to NUL (keepalive logs to keepalive_<name>.log itself)
' cmd /c wraps node so it inherits a hidden console (avoids null-stdout quirks)
' waitOnReturn = TRUE: task stays Running while keepalive lives (CRITICAL: Task Scheduler
' job-object kills the whole child tree once wscript exits - non-wait mode killed node
' right after lock write, proven 2026-09-29)
' ZERO QUOTES: all paths have no spaces, and cmd /c mangles multi-quote commands
' (strips first+last quote then mis-parses) - zero-quote form is the only safe shape
sh.Run "cmd /c " & nodeExe & " keepalive.js " & cfg & " >NUL 2>&1", 0, True
