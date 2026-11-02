# Builds RunWienerLinienDisplay.exe with the C# compiler that ships with Windows (.NET Framework 4).
# Usage:  powershell -ExecutionPolicy Bypass -File launcher\build.ps1 [-Desktop]
#   -Desktop   also copy the .exe to your desktop

param([switch]$Desktop)

$ErrorActionPreference = "Stop"
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Split-Path -Parent $here
$csc     = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$out     = Join-Path $project "RunWienerLinienDisplay.exe"
$icon    = Join-Path $here "icon.ico"

if (-not (Test-Path $csc)) { throw "C# compiler not found at $csc" }

# bake the project folder into the exe, so it also works when copied to the desktop
$info = Join-Path $env:TEMP "kraken_tram_buildinfo.cs"
"static class BuildInfo { public const string ProjectDir = @`"$project`"; }" |
    Set-Content -Encoding UTF8 $info

$cscArgs = @("/nologo", "/target:winexe", "/optimize+", "/out:$out",
             "/reference:System.Management.dll", "/reference:System.Windows.Forms.dll")
if (Test-Path $icon) { $cscArgs += "/win32icon:$icon" }
& $csc @cscArgs (Join-Path $here "RunWienerLinienDisplay.cs") $info
if ($LASTEXITCODE -ne 0) { throw "build failed" }
Remove-Item $info
Write-Host "Built $out"

if ($Desktop) {
    $dest = Join-Path ([Environment]::GetFolderPath("Desktop")) "RunWienerLinienDisplay.exe"
    Copy-Item $out $dest -Force
    Write-Host "Copied to $dest"
}
