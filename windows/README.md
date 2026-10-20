# Windows 启动脚本与控制面板

这一套提供双击 exe / cmd 启动、自动打开看板与托盘常驻；另有无面板的后台运行方式。
源码仓库保存启动器源码和编译脚本，exe 成品由本机编译生成，不直接提交到 Git。

## 推荐入口：双击 `Qoder2API.exe`

安装目录根部的 `Qoder2API.exe` 是控制面板（C# WinForms，`csc` 编译，不依赖 .NET SDK）。
源码根目录默认没有 exe：先按下文编译，再通过部署脚本把成品复制到安装目录根部。

- 双击打开面板，**自动检测并启动服务**（默认 `autostart=1`）
- 启动成功后**自动打开看板** `http://127.0.0.1:8790/`
- 显示状态、PID、版本、账号数、端口占用、运行日志
- 按钮：启动 / 停止 / 重启 / 打开看板 / 保存端口 / 打开目录
- 点关闭 X = **最小化到托盘**，服务继续后台运行；托盘菜单可重新打开面板或退出
- 也支持命令行（脚本 / 计划任务用）：

```powershell
Qoder2API.exe --start     # 启动（已在运行则直接返回 0）
Qoder2API.exe --stop      # 停止
Qoder2API.exe --restart   # 重启
Qoder2API.exe --status    # 退出码 0 = 运行中，1 = 未运行
```

配置写在项目根的 `launcher.ini`：

```ini
port=8790            # 端口
autostart=1          # 面板启动时自动拉起服务（0 = 不自动）
open_after_start=1   # 启动成功后自动打开看板（0 = 不打开）
```

## 其它入口

| 脚本 | 作用 |
|---|---|
| `windows\start.cmd` | 前台启动（无面板）：自动打开看板，关窗口即停 |
| `windows\stop.cmd` | 停止服务：只停命令行含 `qoder_proxy.py` 的进程 |
| `windows\status.cmd` | 查看状态：PID、看板地址、`/health`、日志尾部 |
| `windows\service-tools.ps1` | 后台常驻：`start` / `stop` / `status` / `restart`，日志写项目根 |

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\service-tools.ps1 start
powershell -ExecutionPolicy Bypass -File .\windows\service-tools.ps1 status
```

## 端口

默认 **8790**。改法（优先级从高到低）：

```bat
start.cmd 8791          :: 只影响这一次
setx QD_PORT 8791       :: 用户环境变量，之后每次都生效
```

控制面板的端口以 `launcher.ini` 为准；面板里改完点「保存端口」后重启服务生效。

## Python 从哪来

控制面板和脚本按同一顺序找解释器（只接受 3.9+）：

1. 项目根目录的便携运行时 `python\python.exe`
2. `.runtime\python\python.exe`（与 WorkBuddyManager 的目录约定一致）
3. PATH 上的 `python`、`py`
4. Codex 自带的运行时（仅脚本）

所以现在直接用本机 Python；以后把便携运行时放进 `python\`，换台机器就不用装 Python 了。

## 编译控制面板

改了 `windows\launcher\Qoder2ApiLauncher.cs` 之后：

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\launcher\build.ps1
```

产物 `windows\launcher\dist\Qoder2API.exe`，`windows\deploy.ps1` 会把它同步到**安装目录根部**。
`dist/` 是可再生构建产物，已从 Git 排除；账号、运行配置和日志也不随源码提交。

## 与旧脚本的关系

| 旧脚本 | 区别 |
|---|---|
| `start-qoder-proxy.bat` | 后台分离运行，不自动开浏览器，日志写根目录；行为不变 |
| `stop-qoder-proxy.bat` | 与本目录 `stop.cmd` 都按 PID + 命令行校验，选一个用即可 |

同一时间只应有一份服务在跑：`qoder_proxy.py` 自带单实例检测，第二份会直接退出并提示。

## 全局出口代理

打开看板的「设置 → 出口代理」，填 `http://Clash:真实密码@192.168.0.2:7890`，测试后保存。HTTP 代理支持 HTTPS CONNECT，也支持 SOCKS5 用户名密码；所有账号统一出口，保存后后续请求即时生效。不要把掩码当密码，也不要把真实密码发给别人。

清除会回落到环境变量（没有时才直连）；配置与密码保存在 `accounts\settings.json`，看板只回显掩码。详见[部署说明](../docs/deployment.md#出口代理)。

## 数据与升级

> 代理页面和 exe 是本地定制。上游一键更新会覆盖 Python/看板，可能移除代理功能；升级前请先备份并合并定制改动。

- 账号、面板设置：`accounts\`；请求流水：`usage\`
- 看板里点「一键更新」会替换代码并重启，`accounts\`、`usage\`、`python\` 都会保留
- `*.ps1` 需保持 UTF-8 with BOM（Windows PowerShell 5.1 读取无 BOM 的 UTF-8 会乱码）
- `*.cmd` 必须保持纯 ASCII（cmd.exe 按系统代码页解析批处理）

## 从源码目录重新部署

从仓库根目录运行部署脚本，通过 `-Target` 指定安装位置。以下为通用示例，请替换为自己的目录；已有安装应始终使用原目标，避免创建第二份运行数据。
目标端的 `accounts\`、`usage\`、日志与 `launcher.ini` 保留，不从源码覆盖。

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\deploy.ps1 -Target 'C:\Apps\Qoder2API'
```

不传 `-Target` 时，脚本使用默认安装路径；默认值可在脚本参数处查看。源码目录和安装目录不能相同。
