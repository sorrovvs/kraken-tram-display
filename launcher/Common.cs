// Code shared by RunWienerLinienDisplay.exe and ExitWienerLinienDisplay.exe.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Management;
using System.Threading;
using System.Windows.Forms;

static class Common
{
    public const string Title = "Wiener Linien Display";

    public static string FindProjectDir()
    {
        string exeDir = AppDomain.CurrentDomain.BaseDirectory;
        foreach (string d in new[] { exeDir, Path.Combine(exeDir, ".."), BuildInfo.ProjectDir })
        {
            if (!string.IsNullOrEmpty(d) && File.Exists(Path.Combine(d, "tram_lcd.py")))
                return Path.GetFullPath(d);
        }
        return null;
    }

    // python/pythonw processes running the display (a venv python.exe is a stub that starts
    // the real interpreter as a child, so one display usually shows up as two processes)
    public static List<int> RunningDisplays()
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

    // Returns false if no display was running.
    public static bool StopRunningDisplays(string dir)
    {
        if (RunningDisplays().Count == 0) return false;

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
        return true;
    }

    public static void Fail(string message)
    {
        MessageBox.Show(message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    public static string NotFoundMessage()
    {
        return "Could not find tram_lcd.py.\n\nPut this .exe in the project folder, or rebuild it with " +
               "launcher\\build.ps1 so it knows where the project is.";
    }
}
