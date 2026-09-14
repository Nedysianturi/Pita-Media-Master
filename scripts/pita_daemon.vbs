' Pita Media Autonomous Background Daemon Launcher (VBScript)
' Runs python main.py daemon in complete stealth / zero console window mode
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

' Get project directory from script location
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
ProjectDir = FSO.GetParentFolderName(ScriptDir)

' Path to virtual environment pythonw.exe or python.exe
PythonExe = ProjectDir & "\.venv\Scripts\pythonw.exe"
If Not FSO.FileExists(PythonExe) Then
    PythonExe = ProjectDir & "\.venv\Scripts\python.exe"
End If

CmdLine = """" & PythonExe & """ """ & ProjectDir & "\main.py"" daemon"

' Change working directory to project root
WshShell.CurrentDirectory = ProjectDir

' Run hidden (0 = hide window, False = don't wait for return)
WshShell.Run CmdLine, 0, False
