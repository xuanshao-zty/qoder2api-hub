# 定制分支与安全发布

本分支 `custom/windows-proxy` 保存基于上游 **v1.5.14** 的 Windows 启动器、全局代理与探测修复。
Fork 的 `main` 可继续保留上游更新；创建本分支不会覆盖或回退 `main`，也不意味着已合并它后续的新版本。

## 维护边界

- `origin`：你自己的 Fork，接收定制分支提交。
- `upstream`：原项目，获取后续上游更新。
- 源码仓库：开发、测试、编译；不保存真实账号、API Key、代理密码或运行数据。
- 安装目录：日常运行，保存自己的账号、设置、运行时和日志。源码树与安装树不是目录镜像。

上游升级应在定制分支显式合并、处理冲突并重新测试；不要直接用上游“一键更新”覆盖定制代码。

```console
git fetch upstream
git switch custom/windows-proxy
git merge upstream/main
python tests/run_all.py --jobs 4
```

有冲突时先完成适配与测试，再提交推送；不要用 `--force` 或盲目全部选取一侧来消除冲突。

## 从源码生成 Windows 启动器

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\launcher\build.ps1
powershell -ExecutionPolicy Bypass -File .\windows\deploy.ps1 -Target 'C:\Apps\Qoder2API'
```

编译产物位于 `windows/launcher/dist/`，部署脚本将 exe 复制到**安装目录根部**。
Git 不提交 exe、生成的程序集信息或便携 Python；如需分发可执行成品，应单独构建并通过 Release 发布。
已有安装请把 `-Target` 替换为原安装目录，且不能与源码目录相同。

## 提交前的敏感数据检查

- 只暂存明确需要发布的源码、脚本、测试、图标与文档，不直接 `git add .`。
- 检查 `git diff --cached --name-only` 和 `git diff --cached --check`。
- 不上传账号及其导出、设置、Token、Key、真实代理密码、日志、usage、缓存、备份、PID、`launcher.ini`、`.env` 或本机工具状态。
- 忽略规则不影响已经跟踪的文件；发现此类文件应先取消跟踪，保留本地运行数据。也要检查历史，不能只检查最后一次差异。
- 文件名检查不能替代内容检查；提交前还需检查认证 URL、硬编码凭据、个人路径和图片中的可见秘密。
- 发布前跑完整测试并复核提交树。自动扫描无法保证识别所有无格式口令，发现真实泄漏应停止推送并处理凭据。

TLS 回归中唯一允许的 PEM 是 `tests/fixtures/outbound-proxy/` 内明确标注 **PUBLIC TEST**、仅用于保留域名 `probe.invalid` 的自签证书与配套公开测试密钥。
它们不是真实账号或服务凭据，只用于回环测试的局部 `SSLContext`；不得用于生产或导入系统信任库。
参见该目录中的 README。此例外不能扩大到其他私钥、证书或凭据文件。
