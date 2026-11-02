// Desktop launcher for Kraken Tram Display.
//
// Double-click to:
//   1. close NZXT CAM (it locks the Kraken's USB device)
//   2. stop a display that is already running (so clicking twice = restart)
//   3. start the display hidden, logging to display.log
// If there is no config.toml yet, it opens the setup wizard in a console instead.
// ExitWienerLinienDisplay.exe stops it again.
//
// Build with launcher\build.ps1 (uses the C# compiler that ships with Windows).

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

[assembly: AssemblyTitle("Run Wiener Linien Display")]
[assembly: AssemblyProduct("Kraken Tram Display")]
[assembly: AssemblyCopyright("\u00A9 Sorrow")]

static class Launcher
{
    [STAThread]
    static int Main()
    {
        try
        {
            string dir = Common.FindProjectDir();
            if (dir == null)
            {
                Common.Fail(Common.NotFoundMessage());
                return 1;
            }

            string python = Path.Combine(dir, @".venv\Scripts\python.exe");
            if (!File.Exists(python))
            {
                Common.Fail("Python environment not found:\n" + python + "\n\nInstall it first (see README):\n" +
                            "python -m venv .venv\n.venv\\Scripts\\python.exe -m pip install -r requirements.txt");
                return 1;
            }

            StopNzxtCam();
            Common.StopRunningDisplays(dir);

            if (!File.Exists(Path.Combine(dir, "config.toml")))
            {
                // first run: show the setup wizard in a normal console window
                Process.Start(new ProcessStartInfo("cmd.exe",
                    "/k \"\"" + python + "\" tram_lcd.py setup\"")
                { WorkingDirectory = dir, UseShellExecute = true });
                return 0;
            }

            // run hidden; cmd keeps stdout/stderr flowing into display.log
            Process.Start(new ProcessStartInfo("cmd.exe",
                "/c \"\"" + python + "\" -u tram_lcd.py > display.log 2>&1\"")
            { WorkingDirectory = dir, UseShellExecute = false, CreateNoWindow = true });
            return 0;
        }
        catch (Exception e)
        {
            Common.Fail(e.Message);
            return 1;
        }
    }

    static void StopNzxtCam()
    {
        foreach (Process p in Process.GetProcessesByName("NZXT CAM"))
        {
            try { p.Kill(); p.WaitForExit(5000); } catch { /* already gone or not ours */ }
        }
    }
}
