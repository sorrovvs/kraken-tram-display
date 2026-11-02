// Desktop launcher for Kraken Tram Display.
//
// Double-click to:
//   1. close NZXT CAM (it locks the Kraken's USB device)
//   2. stop a display that is already running (so clicking twice = restart)
//   3. start the display hidden, logging to display.log
// If there is no config.toml yet, it opens the setup wizard in a console instead.
//
// Build with launcher\build.ps1 (uses the C# compiler that ships with Windows).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.IO;
using System.Management;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Run Wiener Linien Display")]
[assembly: AssemblyProduct("Kraken Tram Display")]
[assembly: AssemblyCopyright("© Sorrow")]

static class Launcher
{
    const string Title = "Wiener Linien Display";

    [STAThread]
    static int Main()
    {
        try
        {
            string dir = FindProjectDir();
            if (dir == null)
            {
                Fail("Could not find tram_lcd.py.\n\nPut this .exe in the project folder, or rebuild it with " +
                     "launcher\\build.ps1 so it knows where the project is.");
                return 1;
            }

            string python = Path.Combine(dir, @".venv\Scripts\python.exe");
            if (!File.Exists(python))
            {
                Fail("Python environment not found:\n" + python + "\n\nInstall it first (see README):\n" +
                     "python -m venv .venv\n.venv\\Scripts\\python.exe -m pip install -r requirements.txt");
                return 1;
            }

            StopNzxtCam();
            StopRunningDisplays(dir);

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
            Fail(e.Message);
            return 1;
        }
    }

    static string FindProjectDir()
    {
        string exeDir = AppDomain.CurrentDomain.BaseDirectory;
        foreach (string d in new[] { exeDir, Path.Combine(exeDir, ".."), BuildInfo.ProjectDir })
        {
            if (!string.IsNullOrEmpty(d) && File.Exists(Path.Combine(d, "tram_lcd.py")))
                return Path.GetFullPath(d);
        }
        return null;
    }

    static void StopNzxtCam()
    {
        foreach (Process p in Process.GetProcessesByName("NZXT CAM"))
        {
            try { p.Kill(); p.WaitForExit(5000); } catch { /* already gone or not ours */ }
        }
    }

    // python/pythonw processes running the display (a venv python.exe is a stub that starts
    // the real interpreter as a child, so one display usually shows up as two processes)
    static List<int> RunningDisplays()
    {
        var pids = new List<int>();
        var query = "SELECT ProcessId, CommandLine FROM Win32_Process " +
                    "WHERE Name LIKE 'python%.exe' AND CommandLine LIKE '%tram_lcd.py%'";
        using (var searcher = new ManagementObjectSearcher(query))
        {
            foreach (ManagementObject mo in searcher.Get())
            {
                string cmd = (mo["CommandLine"] ?? "").ToString();
                if (cmd.Contains(" setup") || cmd.Contains(" stop") || cmd.Contains(" preview")) continue;
                pids.Add(Convert.ToInt32(mo["ProcessId"]));
            }
        }
        return pids;
    }

    static void StopRunningDisplays(string dir)
    {
        if (RunningDisplays().Count == 0) return;

        // ask politely first: the display exits between uploads, so the Kraken LCD is never
        // left waiting for the rest of a half-sent image
        string stopFile = Path.Combine(dir, "stop.request");
        File.WriteAllText(stopFile, "");
        for (int i = 0; i < 75 && RunningDisplays().Count > 0; i++)
            Thread.Sleep(200);

        foreach (int pid in RunningDisplays())  // still running after 15 s: force it
        {
            try { Process.GetProcessById(pid).Kill(); } catch { /* already gone */ }
        }
        try { File.Delete(stopFile); } catch { }
        Thread.Sleep(1000);
    }

    static void Fail(string message)
    {
        MessageBox.Show(message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
