Set shell = CreateObject("Wscript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "python """ & folder & "\server.py"""", 0, False
