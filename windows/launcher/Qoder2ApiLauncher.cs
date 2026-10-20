// Qoder2API 控制面板（Windows / .NET Framework 4）
// 双击 exe：打开面板，自动检测 / 启动 qoder_proxy.py，启动成功后自动打开看板。
// 关闭窗口 = 最小化到托盘，服务继续后台运行；托盘菜单「退出」只退出面板。
// 也支持命令行：Qoder2API.exe --start | --stop | --restart | --status
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace Qoder2ApiLauncher
{
    internal sealed class ServerStatus
    {
        public bool Running;
        public int Pid;
        public string Version = "";
        public int Accounts = -1;
        public bool PortInUseByOther;
    }

    internal sealed class LauncherConfig
    {
        private readonly string _path;
        public int Port = 8790;
        public bool AutoStart = true;
        public bool OpenAfterStart = true;

        public LauncherConfig(string root)
        {
            _path = Path.Combine(root, "launcher.ini");
            Load();
        }

        public void Load()
        {
            try
            {
                if (!File.Exists(_path)) return;
                foreach (string raw in File.ReadAllLines(_path))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq).Trim().ToLowerInvariant();
                    string val = line.Substring(eq + 1).Trim();
                    if (key == "port")
                    {
                        int p;
                        if (int.TryParse(val, out p) && p > 0 && p < 65536) Port = p;
                    }
                    else if (key == "autostart")
                    {
                        AutoStart = !(val == "0" || val.Equals("false", StringComparison.OrdinalIgnoreCase));
                    }
                    else if (key == "open_after_start")
                    {
                        OpenAfterStart = !(val == "0" || val.Equals("false", StringComparison.OrdinalIgnoreCase));
                    }
                }
            }
            catch { }
        }

        public void Save()
        {
            try
            {
                File.WriteAllLines(_path, new string[] {
                    "# Qoder2API launcher settings",
                    "port=" + Port,
                    "autostart=" + (AutoStart ? "1" : "0"),
                    "open_after_start=" + (OpenAfterStart ? "1" : "0")
                }, Encoding.UTF8);
            }
            catch { }
        }
    }

    internal sealed class Launcher
    {
        public readonly string Root;
        public readonly string PidFile;
        public readonly string OutLog;
        public readonly string ErrLog;
        public readonly string ScriptPath;
        public readonly LauncherConfig Config;
        public string LastError = "";

        public Launcher(string root)
        {
            Root = root;
            PidFile = Path.Combine(root, "qoder-proxy.pid");
            OutLog = Path.Combine(root, "qoder-proxy.log");
            ErrLog = Path.Combine(root, "qoder-proxy.err.log");
            ScriptPath = Path.Combine(root, "qoder_proxy.py");
            Config = new LauncherConfig(root);
        }

        public string DashboardUrl { get { return "http://127.0.0.1:" + Config.Port + "/"; } }

        public ServerStatus Status()
        {
            ServerStatus st = new ServerStatus();
            int pid = ReadPid();
            if (pid > 0)
            {
                string cmd = GetCommandLine(pid);
                if (cmd != null && cmd.IndexOf("qoder_proxy.py", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    st.Running = true;
                    st.Pid = pid;
                }
            }
            string version;
            int accounts;
            if (FetchHealth(800, out version, out accounts))
            {
                st.Running = true;
                st.Version = version;
                st.Accounts = accounts;
            }
            if (!st.Running && IsPortListening(Config.Port)) st.PortInUseByOther = true;
            return st;
        }

        public bool Start()
        {
            LastError = "";
            if (Status().Running) return true;
            string python = FindPython();
            if (python == null)
            {
                LastError = "没有找到可用的 Python 3.9+（可在 python\\ 放便携运行时）";
                return false;
            }
            string bat = Path.Combine(Path.GetTempPath(), "qoder2api-launcher.bat");
            string line = "\"" + python + "\" \"" + ScriptPath + "\" --port " + Config.Port +
                          " --pid-file \"" + PidFile + "\" > \"" + OutLog + "\" 2> \"" + ErrLog + "\"";
            try
            {
                File.WriteAllLines(bat, new string[] { "@echo off", "cd /d \"" + Root + "\"", line }, Encoding.Default);
                ProcessStartInfo psi = new ProcessStartInfo("cmd.exe", "/c \"" + bat + "\"");
                psi.WorkingDirectory = Root;
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                LastError = ex.Message;
                return false;
            }
            for (int i = 0; i < 40; i++)
            {
                Thread.Sleep(400);
                if (Status().Running) return true;
            }
            LastError = "服务在 16 秒内没有就绪，请查看 qoder-proxy.err.log";
            return false;
        }

        public bool Stop()
        {
            LastError = "";
            ServerStatus st = Status();
            int pid = st.Pid;
            if (pid <= 0) pid = FindPidByCommandLine();
            if (pid <= 0)
            {
                DeletePidFile();
                return !st.Running;
            }
            string cmd = GetCommandLine(pid);
            if (cmd == null || cmd.IndexOf("qoder_proxy.py", StringComparison.OrdinalIgnoreCase) < 0)
            {
                LastError = "PID " + pid + " 不是 qoder-proxy，已拒绝停止";
                return false;
            }
            try
            {
                Process.GetProcessById(pid).Kill();
            }
            catch (Exception ex)
            {
                LastError = ex.Message;
                return false;
            }
            for (int i = 0; i < 25; i++)
            {
                Thread.Sleep(200);
                if (!ProcessExists(pid)) break;
            }
            DeletePidFile();
            return true;
        }

        public void OpenDashboard()
        {
            try { Process.Start(DashboardUrl); } catch { }
        }

        public void OpenFolder()
        {
            try { Process.Start("explorer.exe", Root); } catch { }
        }

        public string ReadLogTail(int maxChars)
        {
            StringBuilder sb = new StringBuilder();
            AppendTail(sb, OutLog, maxChars);
            AppendTail(sb, ErrLog, maxChars);
            return sb.ToString();
        }

        private static void AppendTail(StringBuilder sb, string path, int maxChars)
        {
            try
            {
                if (!File.Exists(path)) return;
                using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read,
                                                     FileShare.ReadWrite | FileShare.Delete))
                {
                    long len = fs.Length;
                    int take = (int)Math.Min(len, maxChars);
                    fs.Seek(-take, SeekOrigin.End);
                    byte[] buf = new byte[take];
                    int read = fs.Read(buf, 0, take);
                    sb.AppendLine("--- " + Path.GetFileName(path) + " ---");
                    sb.Append(Encoding.UTF8.GetString(buf, 0, read));
                }
            }
            catch { }
        }

        private int ReadPid()
        {
            try
            {
                if (!File.Exists(PidFile)) return 0;
                int pid;
                if (int.TryParse(File.ReadAllText(PidFile).Trim(), out pid) && pid > 0) return pid;
            }
            catch { }
            return 0;
        }

        private void DeletePidFile()
        {
            try { if (File.Exists(PidFile)) File.Delete(PidFile); } catch { }
        }

        private static bool ProcessExists(int pid)
        {
            try { Process.GetProcessById(pid); return true; }
            catch { return false; }
        }

        private static bool IsPortListening(int port)
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult ar = client.BeginConnect("127.0.0.1", port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(300)) return false;
                    client.EndConnect(ar);
                    return true;
                }
            }
            catch { return false; }
        }

        private int FindPidByCommandLine()
        {
            try
            {
                using (ManagementObjectSearcher searcher = new ManagementObjectSearcher(
                    "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = 'python.exe'"))
                {
                    foreach (ManagementObject mo in searcher.Get())
                    {
                        string cmd = mo["CommandLine"] as string;
                        if (cmd == null) continue;
                        if (cmd.IndexOf("qoder_proxy.py", StringComparison.OrdinalIgnoreCase) >= 0 &&
                            cmd.IndexOf("--port " + Config.Port, StringComparison.Ordinal) >= 0)
                            return Convert.ToInt32(mo["ProcessId"]);
                    }
                }
            }
            catch { }
            return 0;
        }

        private static string GetCommandLine(int pid)
        {
            try
            {
                using (ManagementObjectSearcher searcher = new ManagementObjectSearcher(
                    "SELECT CommandLine FROM Win32_Process WHERE ProcessId = " + pid))
                {
                    foreach (ManagementObject mo in searcher.Get())
                        return mo["CommandLine"] as string;
                }
            }
            catch { }
            return null;
        }

        private bool FetchHealth(int timeoutMs, out string version, out int accounts)
        {
            version = "";
            accounts = -1;
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(
                    "http://127.0.0.1:" + Config.Port + "/health");
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.Proxy = null;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    string body = sr.ReadToEnd();
                    if (body.IndexOf("\"qoder-proxy\"", StringComparison.Ordinal) < 0) return false;
                    Match m = Regex.Match(body, "\"version\"\\s*:\\s*\"([^\"]*)\"");
                    if (m.Success) version = m.Groups[1].Value;
                    Match a = Regex.Match(body, "\"accounts\"\\s*:\\s*(\\d+)");
                    if (a.Success) accounts = int.Parse(a.Groups[1].Value);
                    return true;
                }
            }
            catch { return false; }
        }

        private string FindPython()
        {
            string[] candidates = new string[] {
                Path.Combine(Root, @"python\python.exe"),
                Path.Combine(Root, @".runtime\python\python.exe")
            };
            foreach (string c in candidates)
            {
                if (File.Exists(c) && TestPython(c)) return c;
            }
            foreach (string name in new string[] { "python", "py" })
            {
                string found = WhichOnPath(name);
                if (found != null && TestPython(found)) return found;
            }
            return null;
        }

        private static string WhichOnPath(string name)
        {
            try
            {
                string path = Environment.GetEnvironmentVariable("PATH") ?? "";
                foreach (string dir in path.Split(';'))
                {
                    string trimmed = dir.Trim();
                    if (trimmed.Length == 0) continue;
                    try
                    {
                        string full = Path.Combine(trimmed, name + ".exe");
                        if (File.Exists(full)) return full;
                    }
                    catch { }
                }
            }
            catch { }
            return null;
        }

        private static bool TestPython(string exe)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe,
                    "-c \"import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)\"");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                using (Process p = Process.Start(psi))
                {
                    if (!p.WaitForExit(5000))
                    {
                        try { p.Kill(); } catch { }
                        return false;
                    }
                    return p.ExitCode == 0;
                }
            }
            catch { return false; }
        }
    }

    internal sealed class MainForm : Form
    {
        private readonly Launcher _launcher;
        private readonly BackgroundWorker _statusWorker = new BackgroundWorker();
        private readonly BackgroundWorker _actionWorker = new BackgroundWorker();
        private Label _lblStatus;
        private Label _lblDetail;
        private Label _lblHint;
        private LinkLabel _lnkDash;
        private Button _btnStart;
        private Button _btnStop;
        private Button _btnRestart;
        private Button _btnDash;
        private Button _btnSavePort;
        private Button _btnFolder;
        private NumericUpDown _nudPort;
        private RichTextBox _txtLog;
        private NotifyIcon _tray;
        private System.Windows.Forms.Timer _timer;
        private bool _exiting;
        private bool _hintShown;

        public MainForm(Launcher launcher)
        {
            _launcher = launcher;
            BuildUi();
            BuildTray();
            _statusWorker.DoWork += StatusWorkerDoWork;
            _statusWorker.RunWorkerCompleted += StatusWorkerCompleted;
            _actionWorker.DoWork += ActionWorkerDoWork;
            _actionWorker.RunWorkerCompleted += ActionWorkerCompleted;
            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 2500;
            _timer.Tick += delegate
            {
                if (!_statusWorker.IsBusy && !_actionWorker.IsBusy) _statusWorker.RunWorkerAsync();
            };
            _timer.Start();
        }

        private void BuildUi()
        {
            Text = "Qoder2API";
            ClientSize = new Size(560, 432);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Microsoft YaHei UI", 9F);
            Icon = SystemIcons.Application;
            BackColor = Color.WhiteSmoke;

            Label title = new Label();
            title.Text = "Qoder2API";
            title.Font = new Font("Microsoft YaHei UI", 16F, FontStyle.Bold);
            title.AutoSize = true;
            title.Location = new Point(16, 10);
            Controls.Add(title);

            _lblStatus = new Label();
            _lblStatus.Text = "● 检测中...";
            _lblStatus.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold);
            _lblStatus.ForeColor = Color.DimGray;
            _lblStatus.AutoSize = true;
            _lblStatus.Location = new Point(18, 50);
            Controls.Add(_lblStatus);

            _lblDetail = new Label();
            _lblDetail.Text = "正在检测服务状态...";
            _lblDetail.ForeColor = Color.Gray;
            _lblDetail.AutoSize = true;
            _lblDetail.Location = new Point(20, 80);
            Controls.Add(_lblDetail);

            _lnkDash = new LinkLabel();
            _lnkDash.Text = _launcher.DashboardUrl;
            _lnkDash.AutoSize = true;
            _lnkDash.Location = new Point(20, 102);
            _lnkDash.LinkColor = Color.DodgerBlue;
            _lnkDash.Click += delegate { _launcher.OpenDashboard(); };
            Controls.Add(_lnkDash);

            _btnStart = NewButton("启动", 18, 136, 120, 36);
            _btnStart.Click += delegate { RunAction("start"); };
            _btnStop = NewButton("停止", 146, 136, 120, 36);
            _btnStop.Click += delegate { RunAction("stop"); };
            _btnRestart = NewButton("重启", 274, 136, 120, 36);
            _btnRestart.Click += delegate { RunAction("restart"); };
            _btnDash = NewButton("打开看板", 402, 136, 140, 36);
            _btnDash.Click += delegate { _launcher.OpenDashboard(); };

            Label portLabel = new Label();
            portLabel.Text = "端口";
            portLabel.AutoSize = true;
            portLabel.Location = new Point(20, 192);
            Controls.Add(portLabel);

            _nudPort = new NumericUpDown();
            _nudPort.Minimum = 1;
            _nudPort.Maximum = 65535;
            _nudPort.Value = _launcher.Config.Port;
            _nudPort.Location = new Point(58, 189);
            _nudPort.Size = new Size(86, 23);
            Controls.Add(_nudPort);

            _btnSavePort = NewButton("保存端口", 154, 187, 96, 27);
            _btnSavePort.Click += delegate
            {
                _launcher.Config.Port = (int)_nudPort.Value;
                _launcher.Config.Save();
                _lblHint.Text = "端口已保存，重启服务后生效";
                _lnkDash.Text = _launcher.DashboardUrl;
                RunStatus();
            };
            _btnFolder = NewButton("打开目录", 258, 187, 96, 27);
            _btnFolder.Click += delegate { _launcher.OpenFolder(); };

            Label logLabel = new Label();
            logLabel.Text = "运行日志";
            logLabel.AutoSize = true;
            logLabel.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold);
            logLabel.Location = new Point(18, 226);
            Controls.Add(logLabel);

            _txtLog = new RichTextBox();
            _txtLog.Location = new Point(18, 246);
            _txtLog.Size = new Size(524, 140);
            _txtLog.ReadOnly = true;
            _txtLog.BackColor = Color.White;
            _txtLog.Font = new Font("Consolas", 8F);
            _txtLog.ScrollBars = RichTextBoxScrollBars.Vertical;
            _txtLog.BorderStyle = BorderStyle.FixedSingle;
            Controls.Add(_txtLog);

            _lblHint = new Label();
            _lblHint.Text = "关闭窗口会最小化到托盘，服务继续运行。";
            _lblHint.ForeColor = Color.DimGray;
            _lblHint.AutoSize = true;
            _lblHint.Location = new Point(18, 398);
            Controls.Add(_lblHint);
        }

        private Button NewButton(string text, int x, int y, int w, int h)
        {
            Button b = new Button();
            b.Text = text;
            b.Location = new Point(x, y);
            b.Size = new Size(w, h);
            b.FlatStyle = FlatStyle.System;
            Controls.Add(b);
            return b;
        }

        private void BuildTray()
        {
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("打开面板", null, delegate { ShowPanel(); });
            menu.Items.Add("启动服务", null, delegate { RunAction("start"); });
            menu.Items.Add("停止服务", null, delegate { RunAction("stop"); });
            menu.Items.Add("打开看板", null, delegate { _launcher.OpenDashboard(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("退出", null, delegate { ExitApp(); });

            _tray = new NotifyIcon();
            _tray.Icon = SystemIcons.Application;
            _tray.Text = "Qoder2API";
            _tray.ContextMenuStrip = menu;
            _tray.Visible = true;
            _tray.DoubleClick += delegate { ShowPanel(); };
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            if (_launcher.Config.AutoStart && !_launcher.Status().Running)
                RunAction("start");
            else
                RunStatus();
        }

        private void RunStatus()
        {
            if (_statusWorker.IsBusy) return;
            _statusWorker.RunWorkerAsync();
        }

        private void RunAction(string command)
        {
            if (_actionWorker.IsBusy) return;
            SetButtonsEnabled(false);
            _lblHint.Text = "正在执行...";
            _actionWorker.RunWorkerAsync(command);
        }

        private void StatusWorkerDoWork(object sender, DoWorkEventArgs e)
        {
            e.Result = _launcher.Status();
        }

        private void StatusWorkerCompleted(object sender, RunWorkerCompletedEventArgs e)
        {
            if (e.Error != null) { _lblHint.Text = "状态刷新失败：" + e.Error.Message; return; }
            ApplyStatus((ServerStatus)e.Result);
            RefreshLog();
        }

        private void ActionWorkerDoWork(object sender, DoWorkEventArgs e)
        {
            string cmd = (string)e.Argument;
            string message = null;
            switch (cmd)
            {
                case "start":
                    message = _launcher.Start() ? "服务已启动" : "启动失败：" + _launcher.LastError;
                    break;
                case "stop":
                    message = _launcher.Stop() ? "服务已停止" : "停止失败：" + _launcher.LastError;
                    break;
                case "restart":
                    _launcher.Stop();
                    Thread.Sleep(700);
                    message = _launcher.Start() ? "服务已重启" : "重启失败：" + _launcher.LastError;
                    break;
            }
            e.Result = new object[] { cmd, message, _launcher.Status() };
        }

        private void ActionWorkerCompleted(object sender, RunWorkerCompletedEventArgs e)
        {
            SetButtonsEnabled(true);
            if (e.Error != null) { _lblHint.Text = "操作失败：" + e.Error.Message; return; }
            object[] result = (object[])e.Result;
            string cmd = (string)result[0];
            string message = result[1] as string;
            ServerStatus st = (ServerStatus)result[2];
            if (message != null) _lblHint.Text = message;
            ApplyStatus(st);
            RefreshLog();
            if (cmd == "start" && st.Running && _launcher.Config.OpenAfterStart) _launcher.OpenDashboard();
        }

        private void ApplyStatus(ServerStatus st)
        {
            if (st.Running)
            {
                _lblStatus.Text = "● 运行中";
                _lblStatus.ForeColor = Color.SeaGreen;
                string detail = "PID " + (st.Pid > 0 ? st.Pid.ToString() : "未知");
                if (st.Version.Length > 0) detail += "    版本 " + st.Version;
                if (st.Accounts >= 0) detail += "    账号 " + st.Accounts;
                _lblDetail.Text = detail;
            }
            else
            {
                _lblStatus.Text = "● 已停止";
                _lblStatus.ForeColor = Color.Firebrick;
                _lblDetail.Text = st.PortInUseByOther
                    ? "端口 " + _launcher.Config.Port + " 被其它程序占用，请换个端口"
                    : "点击「启动」运行 Qoder2API";
            }
            _lnkDash.Text = _launcher.DashboardUrl;
            _btnStart.Enabled = !st.Running;
            _btnStop.Enabled = st.Running;
            _btnRestart.Enabled = st.Running;
        }

        private void RefreshLog()
        {
            try
            {
                string tail = _launcher.ReadLogTail(6000);
                if (_txtLog.Text != tail) _txtLog.Text = tail;
            }
            catch { }
        }

        private void SetButtonsEnabled(bool enabled)
        {
            _btnStart.Enabled = enabled;
            _btnStop.Enabled = enabled;
            _btnRestart.Enabled = enabled;
            _btnDash.Enabled = enabled;
            _btnSavePort.Enabled = enabled;
        }

        private void ShowPanel()
        {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
        }

        private void ExitApp()
        {
            _exiting = true;
            _timer.Stop();
            _tray.Visible = false;
            Close();
            Application.Exit();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!_exiting && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                if (!_hintShown)
                {
                    _hintShown = true;
                    _tray.ShowBalloonTip(3000, "Qoder2API 仍在运行",
                        "面板已最小化到托盘，双击图标可重新打开。", ToolTipIcon.Info);
                }
                return;
            }
            _timer.Stop();
            if (_tray != null) { _tray.Visible = false; _tray.Dispose(); }
            base.OnFormClosing(e);
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            Launcher launcher = new Launcher(root);

            if (args.Length > 0)
            {
                string cmd = args[0].ToLowerInvariant();
                if (cmd == "--start") return launcher.Start() ? 0 : 2;
                if (cmd == "--stop") return launcher.Stop() ? 0 : 2;
                if (cmd == "--restart")
                {
                    launcher.Stop();
                    Thread.Sleep(700);
                    return launcher.Start() ? 0 : 2;
                }
                if (cmd == "--status") return launcher.Status().Running ? 0 : 1;
            }

            bool created;
            using (Mutex mutex = new Mutex(true, "Qoder2ApiLauncherPanel", out created))
            {
                if (!created)
                {
                    MessageBox.Show("Qoder2API 控制面板已经在运行（看任务栏或托盘）。",
                        "Qoder2API", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 0;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MainForm(launcher));
            }
            return 0;
        }
    }
}
