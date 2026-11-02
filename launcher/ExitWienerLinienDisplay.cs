// Desktop "exit" button for Kraken Tram Display.
//
// Double-click to stop a running display cleanly (between two uploads); the Kraken LCD goes back
// to showing the liquid temperature. If it hasn't exited after 15 s, it is force-closed.
//
// Build with launcher\build.ps1 (uses the C# compiler that ships with Windows).

using System;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Exit Wiener Linien Display")]
[assembly: AssemblyProduct("Kraken Tram Display")]
[assembly: AssemblyCopyright("\u00A9 Sorrow")]

static class Exiter
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

            if (!Common.StopRunningDisplays(dir))
            {
                MessageBox.Show("The display isn't running.", Common.Title,
                                MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            return 0;
        }
        catch (Exception e)
        {
            Common.Fail(e.Message);
            return 1;
        }
    }
}
