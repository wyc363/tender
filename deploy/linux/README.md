# Tender Review Linux Demo 部署手册

本目录部署当前已验收的**单审查点 POC / Demo**。系统真实执行 `index=12`（地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件）；其余审查点仍在接入，业务准确率仍在优化，输出结果需要专家复核。

禁止将本版本描述为“完整31项审查系统”。

## 目录约定

```text
/opt/tender_review/
├── tender_review_system/
├── venv_main/       # Python 3.12
├── venv_docling/    # Python 3.11
├── outputs/
├── logs/
└── deploy/
```

Windows venv 和 Conda 环境不能复制到 Linux。两个 Linux 环境必须重新创建。

## Gate D1：服务器环境探测

输入条件：可以 SSH 登录，尚未修改系统 Python。

```bash
cd /opt/tender_review/tender_review_system
bash deploy/linux/check_server.sh
```

正常预期：输出发行版、内核、CPU、内存、磁盘、Python、locale 和公网 HTTPS 状态。建议至少4核、8GB内存、30GB可用磁盘，locale 使用 UTF-8。

常见失败点：无公网出站、磁盘不足、系统时间错误、非 UTF-8 locale。

验收条件：资源满足且能够访问 Python 包源和模型 API endpoint。

## Gate D2：目录、源码和双环境

### 1. 创建目录

```bash
bash deploy/linux/bootstrap_dirs.sh
```

首次创建 `/opt/tender_review` 需要 sudo。脚本可重复执行，不会覆盖源码。

### 2. 创建主环境

Ubuntu 24.04 示例：

```bash
sudo apt-get install -y python3.12-venv curl ca-certificates
python3.12 -m venv /opt/tender_review/venv_main
/opt/tender_review/venv_main/bin/python -m pip install --upgrade pip setuptools wheel
/opt/tender_review/venv_main/bin/python -m pip install \
  -r /opt/tender_review/tender_review_system/deploy/linux/requirements-main.txt
```

其他发行版使用其包管理器提供 Python 3.12，不替换系统 Python。

### 3. 创建 Docling 环境

服务器没有 Python 3.11 时，可用 `uv` 管理独立解释器：

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
sh /tmp/uv-install.sh
rm -f /tmp/uv-install.sh

$HOME/.local/bin/uv venv --python 3.11 /opt/tender_review/venv_docling
$HOME/.local/bin/uv pip install \
  --python /opt/tender_review/venv_docling/bin/python \
  --index-url https://download.pytorch.org/whl/cpu \
  torch torchvision
$HOME/.local/bin/uv pip install \
  --python /opt/tender_review/venv_docling/bin/python \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match \
  -r /opt/tender_review/tender_review_system/deploy/linux/requirements-docling.txt
```

这里先安装 CPU 版 PyTorch，避免无 GPU Demo 服务器下载数GB CUDA运行库。

### 4. 配置 `.env`

```bash
cp deploy/linux/.env.example 05_pipeline_local/.env
chmod 600 05_pipeline_local/.env
```

填写真实 Parser/Judge API 配置。不得把 Key 写进源码、YAML、systemd unit、报告或日志。

### 5. 离线环境检查

```bash
bash deploy/linux/smoke_main_env.sh
bash deploy/linux/smoke_docling.sh
```

正常预期：主环境 import、项目配置和 Docling import 均为 PASS。两个脚本不访问 file_url、不调用模型、不运行完整 Pipeline。

验收条件：Python分别为3.12/3.11，依赖导入成功，Linux配置解析成功。

## Gate D3：服务器本机启动

先使用两个 SSH 窗口手动启动，不要混成一个进程。

窗口A：

```bash
bash /opt/tender_review/tender_review_system/deploy/linux/start_api.sh
```

窗口B：

```bash
bash /opt/tender_review/tender_review_system/deploy/linux/start_worker.sh
```

另一个窗口检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/openapi.json
```

OpenAPI中必须出现 `POST /api/v1/review`。API不会启动Worker，Worker也不会启动API。

可选的本地PDF Docling smoke：

```bash
bash deploy/linux/smoke_docling.sh /path/to/local.pdf
```

该命令始终使用模块方式：

```text
python -m review_pipeline.docling_adapter
```

不得改回直接执行 `review_pipeline/docling_adapter.py`。

常见失败点：`.env` 权限或变量缺失、outputs不可写、Docling原生wheel不兼容、路径大小写错误。

## Gate D4：公网访问和真实E2E

同时检查：

1. Linux firewall；
2. 云平台安全组或机房边界防火墙；
3. TCP 8000 是否映射到本机；
4. Uvicorn是否监听 `0.0.0.0:8000`。

```bash
ss -lntp | grep ':8000 '
```

优先只允许甲方出口 IP 访问8000。若测试阶段临时全网开放，完成后立即收紧。

外部检查：

```powershell
Invoke-RestMethod "http://<SERVER_IP>:8000/health"
```

真实 E2E 使用甲方请求格式调用 `POST /api/v1/review`，验收：HTTP 200、耗时小于1800秒、最外层只有 `id/model/usage/choices`、`message.content` 是JSON字符串、`result` 只有真实 `index=12`，且无 `log_file/job_id`。

当前同步连接最长1800秒。Demo阶段建议直接访问Uvicorn 8000，不主动增加Nginx、Cloudflare Proxy、负载均衡或其他可能具有60/120/300秒 idle timeout 的代理。以后增加代理时，上游/read/idle timeout 必须大于1800秒。

## Gate D5：systemd常驻

仅在手动验证通过后安装：

```bash
sudo install -m 644 deploy/linux/tender-review-api.service /etc/systemd/system/
sudo install -m 644 deploy/linux/tender-review-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tender-review-api tender-review-worker
```

检查和运维：

```bash
systemctl status tender-review-api tender-review-worker
sudo systemctl restart tender-review-api tender-review-worker
journalctl -u tender-review-api -f
journalctl -u tender-review-worker -f
```

两个unit均使用 `/opt/tender_review/tender_review_system/05_pipeline_local/.env`，API Key不写入unit。服务失败时自动重启，运行用户为专用系统账号 `tender-review`。

## 安全边界

- `.env` 必须是600；
- 不向甲方开放SSH；
- outputs不配置为静态下载目录；
- 不在日志中打印完整 signed URL 或 API Key；
- 8000优先限制来源IP；
- `/docs` 仅Demo阶段开放；
- 本版本没有HTTPS、认证、Nginx和正式生产网关，不应作为正式生产部署。


