' Pita Media - Desktop Shortcut Creator (VBScript)
' Creates "Pita Media.lnk" on the Desktop with the official 3D logo icon
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
ProjectDir = FSO.GetParentFolderName(ScriptDir)
DesktopPath = WshShell.SpecialFolders("Desktop")

ShortcutPath = DesktopPath & "\Pita Media.lnk"
Set Shortcut = WshShell.CreateShortcut(ShortcutPath)

' Target is the open_dashboard.bat or direct browser command
Shortcut.TargetPath = ProjectDir & "\scripts\open_dashboard.bat"
Shortcut.WorkingDirectory = ProjectDir
Shortcut.Description = "Pita Media — Web Command Center & Autonomous Content Engine"

' Set custom icon
IconPath = ProjectDir & "\storage\logo.ico"
If FSO.FileExists(IconPath) Then
    Shortcut.IconLocation = IconPath & ", 0"
End If

Shortcut.WindowStyle = 7 ' Minimized / silent launch
Shortcut.Save

WScript.Echo "[SUCCESS] Shortcut Desktop 'Pita Media' berhasil dibuat."
