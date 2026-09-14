' Pita Media Web Control Dashboard Launcher (VBScript)
' Runs python main.py dashboard silently in the background on port 80 (http://pitamedia.localhost)
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
ProjectDir = FSO.GetParentFolderName(ScriptDir)

PythonExe = ProjectDir & "\.venv\Scripts\pythonw.exe"
If Not FSO.FileExists(PythonExe) Then
    PythonExe = ProjectDir & "\.venv\Scripts\python.exe"
End If

CmdLine = """" & PythonExe & """ """ & ProjectDir & "\main.py"" dashboard"

WshShell.CurrentDirectory = ProjectDir
WshShell.Run CmdLine, 0, False
