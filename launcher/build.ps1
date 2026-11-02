# Builds RunWienerLinienDisplay.exe and ExitWienerLinienDisplay.exe with the C# compiler that ships
# with Windows (.NET Framework 4).
# Usage:  powershell -ExecutionPolicy Bypass -File launcher\build.ps1 [-Desktop]
#   -Desktop   also copy both .exe files to your desktop

param([switch]$Desktop)

$ErrorActionPreference = "Stop"
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Split-Path -Parent $here
$csc     = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$common  = Join-Path $here "Common.cs"

if (-not (Test-Path $csc)) { throw "C# compiler not found at $csc" }

# bake the project folder into the exe, so it also works when copied to the desktop
$info = Join-Path $env:TEMP "kraken_tram_buildinfo.cs"
"static class BuildInfo { public const string ProjectDir = @`"$project`"; }" |
    Set-Content -Encoding UTF8 $info

$targets = @(
    @{ Name = "RunWienerLinienDisplay";  Icon = "icon.ico" },
    @{ Name = "ExitWienerLinienDisplay"; Icon = "icon-exit.ico" }
)

try {
    foreach ($t in $targets) {
        $out  = Join-Path $project "$($t.Name).exe"
        $icon = Join-Path $here $t.Icon
        $cscArgs = @("/nologo", "/target:winexe", "/optimize+", "/out:$out",
                     "/reference:System.Management.dll", "/reference:System.Windows.Forms.dll")
        if (Test-Path $icon) { $cscArgs += "/win32icon:$icon" }
        & $csc @cscArgs (Join-Path $here "$($t.Name).cs") $common $info
        if ($LASTEXITCODE -ne 0) { throw "build of $($t.Name) failed" }
        Write-Host "Built $out"

        if ($Desktop) {
            $dest = Join-Path ([Environment]::GetFolderPath("Desktop")) "$($t.Name).exe"
            Copy-Item $out $dest -Force
            Write-Host "Copied to $dest"
        }
    }
}
finally {
    Remove-Item $info -ErrorAction SilentlyContinue
}
