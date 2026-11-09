<!-- mcp-name: io.github.vmware-skills/vmware-vks -->

# VMware VKS

[![PyPI](https://img.shields.io/pypi/v/vmware-vks)](https://pypi.org/project/vmware-vks/) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#readme-license)

[English](README.md) | 中文

维护者: Wei Zhou · [zhouwei008@gmail.com](mailto:zhouwei008@gmail.com)

社区维护的开源项目，非 VMware 或 Broadcom 官方产品。

[概述](#readme-overview) · [快速开始](#readme-quick-start) · [MCP 配置](#readme-mcp-configuration) · [能力与工作流](#readme-capabilities-and-workflows) · [安全与审计](#readme-safety-and-audit) · [故障排查](#readme-troubleshooting) · [配套 Skills](#readme-companion-skills) · [参考资料](#readme-reference) · [许可证](#readme-license)

<a id="readme-overview"></a>

## 概述

默认通过本地 Stdio 运行；请在本机配置所需目标和凭据。

MCP Skill + CLI，用于 VMware vSphere with Tanzu (VKS) 管理 — Supervisor 集群、vSphere 命名空间和 TanzuKubernetesCluster 生命周期。25 个 MCP 工具。


MCP 默认使用本地 stdio。启用可选 HTTPS 后会打开配置的监听端口，并要求 TLS 与调用者认证；此时属于网络服务。凭据示例先创建仅当前用户可读写的占位文件，拒绝覆盖已有文件，再由用户在本地编辑秘密值。

Skills.sh 示例分别固定安装器 `skills@1.7.6`（需 Node.js >=22.20.0）和仓库 release tag；这不等于锁定传递依赖。

完整的 Agent 指令见 [`skills/vmware-vks/SKILL.md`](skills/vmware-vks/SKILL.md).

<a id="readme-quick-start"></a>

## 快速开始

需要 Python `>=3.10` 和 `uv`。

```bash
uv tool install vmware-vks==3.6.1
vmware-vks --help
```

目标、凭据和本地数据目录设置见 [setup guide](skills/vmware-vks/references/setup-guide.md).

<details>
<summary>安装与配置详情（含离线安装）</summary>

### 前置要求

- **Python 3.10+** — `uv tool install` 所需
- **vSphere 8.0+** — Workload Management（Supervisor）API 要求 vSphere 8.x
- **Workload Management 已启用** — 至少一个计算集群已开启 WCP
- **许可证** — vSphere with Tanzu（Enterprise Plus 或 VMware Cloud Foundation）

配置完成后运行 `vmware-vks check` 验证所有要求是否满足。


### 快速开始

```bash
# 安装
uv tool install vmware-vks==3.6.1

# 配置
mkdir -p ~/.vmware-vks
cp config.example.yaml ~/.vmware-vks/config.yaml
# 编辑 config.yaml，填入 vCenter 主机和用户名

# 每个 target 一个密码环境变量，变量名由 config.yaml 里的 target 名派生：
# target "vcenter01"（config.example.yaml 自带的那个）-> VMWARE_VKS_VCENTER01_PASSWORD
(
  umask 077
  set -C  # 拒绝覆盖已有文件；已有 .env 请直接用本地编辑器修改
  cat > ~/.vmware-vks/.env <<'EOF'
VMWARE_VKS_VCENTER01_PASSWORD=REPLACE_IN_LOCAL_EDITOR
EOF
)
# 继续前，先用本地编辑器填写 .env；不要在终端命令中输入真实秘密值。

# 验证
vmware-vks check

# 常用操作
vmware-vks supervisor status domain-c1
vmware-vks namespace list
vmware-vks tkc list
vmware-vks tkc create my-cluster -n dev --version v1.28.4+vmware.1 --vm-class best-effort-large
vmware-vks tkc create my-cluster -n dev --apply
```

模板只写入占位符。创建后用本地编辑器填写密码或令牌；不要把真实凭据粘贴到终端命令、聊天或提交中。若文件已存在，上述命令会拒绝覆盖；直接编辑现有文件并先确认权限为 `600`。


### 离线 / 气隙环境安装（从源码）

本项目采用现代 PEP 517 构建系统（hatchling），因此**没有 `setup.py`** 是设计使然，
而非缺失文件。若你克隆源码后遇到 `ERROR: File "setup.py" or "setup.cfg" not found
... editable mode currently requires a setuptools-based build`，说明你的 `pip` 版本
低于 21.3，无法用非 setuptools 后端做**可编辑**（`-e`）安装。可编辑模式只是开发
便利，运行本工具并不需要它——任选其一：

```bash
# 从源码树安装 —— 普通（非可编辑）安装会构建 wheel：
pip install .              # 不是  pip install -e .

# ……或先升级 pip，可编辑安装也能用：
pip install --upgrade pip && pip install -e .
```

对于**真正的气隙主机**，在联网机器上构建好 wheel 再拷贝过去——目标主机无需联网：

```bash
# 在联网机器上，把本包及其依赖收集为 wheel：
pip wheel . -w dist        # → dist/*.whl   （或：uv build，仅构建本包）

# 把 dist/ 拷到气隙主机，然后离线安装：
pip install --no-index --find-links dist vmware-vks
```


</details>

<a id="readme-mcp-configuration"></a>

## MCP 配置

将以下配置复制到 MCP 客户端的本地 Stdio 配置中。客户端需要能找到 `uvx`；首次安装所需包时需要访问包源。

```json
{
  "mcpServers": {
    "vmware-vks": {
      "command": "uvx",
      "args": ["--from", "vmware-vks==3.6.1", "vmware-vks-mcp"],
      "env": {
        "VMWARE_VKS_CONFIG": "~/.vmware-vks/config.yaml"
      }
    }
  }
}
```

以下保留预先安装 CLI 的用法及各工具特有的 MCP 选项。

<details>
<summary>预先安装的 CLI 与其他 MCP 选项</summary>

### MCP Server 配置

**v1.5.15+ 推荐方式**：完成 `uv tool install vmware-vks==3.6.1` 后，**一条命令启动 MCP**：

```bash
vmware-vks mcp
```

```json
{
  "mcpServers": {
    "vmware-vks": {
      "command": "vmware-vks",
      "args": ["mcp"],
      "env": {
        "VMWARE_VKS_CONFIG": "~/.vmware-vks/config.yaml"
      }
    }
  }
}
```

<details>
<summary>备选方案：uvx（不安装）或 legacy 入口</summary>

```bash
# 不想安装，临时运行（每次需要联网 resolve 依赖）
uvx --from vmware-vks==3.6.1 vmware-vks mcp

# 旧 entry point（仍可用，向后兼容）
vmware-vks-mcp
```

> **公司 TLS 代理网络下？** uvx 可能报 `invalid peer certificate: UnknownIssuer`。
> 推荐使用上面的 `vmware-vks mcp`（无需联网），或 `export UV_NATIVE_TLS=true`。

</details>


</details>

<a id="readme-capabilities-and-workflows"></a>

## 能力与工作流

### 常用工作流

#### 部署新 TKC 集群

1. 检查兼容性 → `vmware-vks check`
2. 查看可用 K8s 版本 → `vmware-vks tkc versions -n dev`
3. 创建命名空间（如需）→ `vmware-vks namespace create dev --cluster domain-c1 --storage-policy <策略ID> --cpu 16000 --memory 32768 --apply`（策略 ID 通过 `vmware-vks supervisor storage-policies` 获取）
4. 创建 TKC 集群 → `vmware-vks tkc create dev-cluster -n dev --version v1.28.4+vmware.1 --control-plane 1 --workers 3 --vm-class best-effort-large --apply`
5. 获取 kubeconfig（凭据；仅在需要访问集群时）→ `vmware-vks kubeconfig get dev-cluster -n dev -o ./kubeconfig` — 写入仅属主可读的文件，而不是把 token 打印出来

#### 扩容工作节点（压测场景）

1. 查看当前状态 → `vmware-vks tkc get dev-cluster -n dev`
2. 扩容 → `vmware-vks tkc scale dev-cluster -n dev --workers 6`
3. 监控进度 → `vmware-vks tkc get dev-cluster -n dev`（观察 phase）
4. 测试结束后缩容

#### 命名空间资源管理

1. 列出命名空间 → `vmware-vks namespace list`
2. 查看使用情况 → `vmware-vks storage -n dev`
3. 更新配额 → `vmware-vks namespace update dev --cpu 32000 --memory 65536`

### 工具参考（25 个工具）

#### Supervisor

| 工具 | 描述 | 类型 |
|------|------|------|
| `check_vks_compatibility` | vCenter 版本检查 + WCP 状态 | 只读 |
| `get_supervisor_upgrade_risk` | vCenter 9.1.0/9.1.1 升级前公告适用性；VUMDB 架构仍为未测得，并指向官方检查器 | 只读 |
| `get_supervisor_status` | Supervisor 集群状态和 K8s API 端点 | 只读 |
| `list_supervisor_storage_policies` | vCenter 存储策略列表（策略 ID、名称、描述） | 只读 |

从 vCenter 8.0 Update 3 或 9.0.0–9.0.2 升级到 9.1.0/9.1.1 前，先运行
`supervisor upgrade-risk`。报告无法检查 VUMDB 架构，也不代表升级获准。
若 Supervisor/VKS 环境在公告范围内，请按 [Broadcom 官方检查与修复说明](https://knowledge.broadcom.com/external/article/455737)
或 VDT 检查，并在升级前备份 vCenter 和 Supervisor。

#### 命名空间

| 工具 | 描述 | 类型 |
|------|------|------|
| `list_namespaces` | 所有 vSphere 命名空间及状态 | 只读 |
| `get_namespace` | 命名空间详情（配额、存储、角色） | 只读 |
| `create_namespace` | 创建命名空间；默认只预览（`confirm=True` 才执行） | 写操作 |
| `update_namespace` | 修改配额和存储策略 | 写操作 |
| `delete_namespace` | 删除（含 TKC 保护，存在集群时拒绝） | 写操作 |
| `list_vm_classes` | TKC 节点规格可用的 VM 类 | 只读 |

#### TKC

| 工具 | 描述 | 类型 |
|------|------|------|
| `list_tkc_clusters` | TanzuKubernetesCluster 列表及状态 | 只读 |
| `get_tkc_cluster` | 集群详情（所有工作节点池、可计算的副本总数、健康与条件） | 只读 |
| `get_tkc_available_versions` | Supervisor 上支持的 K8s 版本 | 只读 |
| `create_tkc_cluster` | 创建 TKC；默认只预览 YAML 计划（`confirm=True` 才执行） | 写操作 |
| `scale_tkc_cluster` | 调整工作节点数量 | 写操作 |
| `upgrade_tkc_cluster` | 升级 K8s 版本 | 写操作 |
| `delete_tkc_cluster` | 删除（含工作负载保护） | 写操作 |

`get_tkc_cluster.worker_pools` 列出所有已声明的工作节点池。仅当每个池都有已知的固定
副本数时，`worker_replica_total` 才给出合计；自动扩缩容或无法读取的池会使其为 `null`。
旧字段 `worker_replicas` 为兼容性保留，**只代表第一个池**，不能当作集群总数。

#### 访问

| 工具 | 描述 | 类型 |
|------|------|------|
| `issue_supervisor_kubeconfig` | Supervisor kubeconfig — 凭据（bearer token）；内联返回或写入仅属主可读的文件 | 写 |
| `issue_tkc_kubeconfig` | TKC kubeconfig — 凭据（bearer token）；内联返回或写入仅属主可读的文件 | 写 |
| `get_harbor_info` | 内置 Harbor 仓库信息（ID、集群、版本、URL、健康状态、已用存储） | 只读 |
| `list_namespace_storage_usage` | PVC 列表和容量统计 | 只读 |

**VCF 9.1 TKC 访问：** 一份 9.1 真机报告显示，`issue_tkc_kubeconfig` 将 Supervisor JWT 写入 guest kubeconfig 后，TKC API 返回 401。生成文件不代表 guest 访问已验证；使用前请向 guest API 实测。见 [issue #27](https://github.com/vmware-skills/VMware-VKS/issues/27)。该报告中的 Supervisor 范围 TKC 读取不受影响。

#### VM Service（vm-operator）

| 工具 | 说明 | 类型 |
|------|------|------|
| `list_vm_service_snapshots` | vSphere Namespace 中的 VirtualMachineSnapshot 对象 | 只读 |
| `list_vm_service_groups` | Namespace 中的 VirtualMachineGroup 对象及其启动顺序 | 只读 |
| `list_vm_service_network_interfaces` | 单个 VM Service VirtualMachine 的网卡（多网卡） | 只读 |

#### 权限自检

| 工具 | 说明 | 类型 |
|------|------|------|
| `get_access_info` | server 使用哪个账号，以及它在根文件夹上是否拥有各工具所需的 vSphere 权限。Kubernetes 调用由命名空间自身的权限决定，此工具不检查 | 只读 |

### CLI 参考

```bash
# 环境检查
vmware-vks check

# Supervisor
vmware-vks supervisor status <集群ID>
vmware-vks supervisor upgrade-risk --to 9.1.1
vmware-vks supervisor storage-policies

# 命名空间
vmware-vks namespace list
vmware-vks namespace get <名称>
vmware-vks namespace create <名称> --cluster <ID> --storage-policy <策略>
vmware-vks namespace create <名称> --cluster <ID> --storage-policy <策略> --apply
vmware-vks namespace update <名称> [--cpu <MHz>] [--memory <MiB>]
vmware-vks namespace delete <名称>
vmware-vks namespace vm-classes

# TKC
vmware-vks tkc list [-n <命名空间>]
vmware-vks tkc get <名称> -n <命名空间>
vmware-vks tkc versions -n <命名空间>
vmware-vks tkc create <名称> -n <命名空间> [--version <版本>] [--vm-class <类型>]
vmware-vks tkc create <名称> -n <命名空间> --apply
vmware-vks tkc scale <名称> -n <命名空间> --workers <数量>
vmware-vks tkc upgrade <名称> -n <命名空间> --version <版本>
vmware-vks tkc delete <名称> -n <命名空间>

# Kubeconfig
vmware-vks kubeconfig supervisor -n <命名空间> [-o <文件路径>]
vmware-vks kubeconfig get <集群名称> -n <命名空间> [-o <文件路径>]

# Harbor 和存储
vmware-vks harbor
vmware-vks storage -n <命名空间>
```

<a id="readme-safety-and-audit"></a>

## 安全与审计

### 安全性

| 特性 | 说明 |
|------|------|
| 以只读为主 | 25 个工具中 16 个为只读 |
| 默认只预览 | `create_namespace`、`create_tkc_cluster`、`delete_namespace`、`delete_tkc_cluster` 接受 `confirm`（默认 `False`）：不带它的调用不做任何变更，只返回 `blast_radius` — 将创建什么，或删除会销毁的 TKC 集群、VM、PVC、节点与工作负载。存在阻断项或影响范围有任何部分读不到时，`confirm=True` 会被拒绝。旧的 `confirmed` / `dry_run` 别名已在 3.4.2 移除，仍传它们会被当作未知参数拒绝 |
| TKC 保护 | `delete_namespace` 在命名空间内存在 TKC 集群时拒绝执行 |
| 工作负载保护 | `delete_tkc_cluster` 在 Deployment/StatefulSet 运行时拒绝执行 |
| 凭据安全 | 密码仅从环境变量（`.env` 文件）加载，不写入 `config.yaml` |
| Kubeconfig 内存构建 | 本 skill 自身调用 API 时，Supervisor/TKC kubeconfig（含 Supervisor bearer token）以内存 dict 形式构建并通过 `load_kube_config_from_dict()` 加载 — 不会写入磁盘临时文件（v1.5.18+） |
| Kubeconfig 属于凭据访问 | `issue_supervisor_kubeconfig` / `issue_tkc_kubeconfig` 返回有效期以小时计的 bearer token：标注 `readOnlyHint: false`（客户端会先询问）、`risk_level: medium`，审计记录中 kubeconfig 被脱敏。仅在用户明确要求时调用；用 `output_path` / `-o` 导出到仅属主可读（0600）的文件 |
| 审计日志 | 所有写操作记录到 `~/.vmware-vks/audit.log` |
| MCP 传输 | 默认本地 stdio；可选的认证 HTTPS 会打开配置的监听端口 |

### 远程访问（HTTPS）与限制工具

默认情况下 `vmware-vks mcp` 通过 stdio 服务一个本地客户端，并提供全部工具。以下两个选项可以改变这一点
（需 vmware-policy ≥ 1.21.0）。

**通过 HTTPS 提供服务**，让其他机器上的客户端连接。每个调用者都需要一个 bearer token：

```bash
vmware-audit token create alice          # 只显示一次，交给 alice
vmware-audit token list                  # 只列名字
vmware-audit token revoke alice          # 下一个请求起即拒绝，无需重启
vmware-vks mcp --transport https --host 0.0.0.0 --port 8443 \
    --cert /etc/vmware-mcp/server.pem --key /etc/vmware-mcp/server-key.pem \
    --public-name mcp.example.com
```

连接客户端（以 Claude Code 为例；任何支持 streamable HTTP 和自定义请求头的 MCP 客户端都可以）：

```bash
claude mcp add --transport http vmware-vks https://mcp.example.com:8443/mcp \
    --header "Authorization: Bearer $VMWARE_MCP_TOKEN"
```

```json
{
  "mcpServers": {
    "vmware-vks": {
      "type": "http",
      "url": "https://mcp.example.com:8443/mcp",
      "headers": { "Authorization": "Bearer ${VMWARE_MCP_TOKEN}" }
    }
  }
}
```

- 没有 token 时服务不会启动；`--public-name` 之外的 Host/Origin 一律拒绝；绑定端口前会自检，确认未认证请求得到 401。
  证书要用客户端信任、且覆盖这些名字的证书。
- 在同一台机器上由反向代理终结 TLS 时，用 `--transport http --host 127.0.0.1 --public-name mcp.example.com`，
  并让代理透传 `Authorization` 头。
- 负载均衡或网关在另一台机器上时，保持 `--transport https`（自签证书也可以），在 `--public-name` 里写上负载均衡
  转发时带的 `Host` 名，并让它透传 `Authorization`。健康检查可以用 `GET /healthz`：不需要 token，只返回
  `{"status": "ok"}`（需 vmware-policy ≥ 1.23.0）。
- 每个请求带上 `traceparent` 或 `X-Request-ID`，它会记进审计记录；用 `vmware-audit log --correlation-id ID`
  就能查到平台上某一次请求对应的调用。
- 会读写本机文件的工具（这里是：`issue_*_kubeconfig(output_path)`）永远碰不到本机的配置、token、审计日志和 TLS 私钥；通过网络访问时，
  除非用 `VMWARE_MCP_FILE_ROOT` 指定一个目录，否则这些工具不能访问本机文件（需 vmware-policy ≥ 1.23.1）。
- 审计日志把每个调用者记为 `token:<名字>`。调用并行执行，同一 target 上也一样：server 为每个 target 最多保持 `VMWARE_MCP_POOL_SIZE`（默认 2）个会话，每个会话都是在该 target 上的一次登录；一个调用最多等 300 秒拿到空闲会话（vmware-policy ≥ 1.22.0）。
- **能连到这个端点的人，都以该 server 配置的账号操作。** token 只用来识别调用者，不在平台上授予任何权限。
  每个权限级别单独起一个端点。`vmware-vks access-info` 可查看是哪个账号、它能做什么。
- **一个端点给多人或多种角色用：** 先运行一次 `vmware-audit roles enable`，再给每个 token 指定角色（`vmware-audit token create 名字 --role reader`，或在 `roles.yaml` 里定义可用的工具和 target；需 vmware-policy ≥ 1.24.0）。角色只能限定这个 token 在本端点上能调用什么，不能超出账号本身的权限。详见 vmware-policy README 的「网络调用者的角色」一节。

**只提供读工具，加上你选定的写工具：**

```bash
vmware-vks mcp --expose read --allow-write create_namespace
# 或：VMWARE_VKS_MCP_EXPOSE=read VMWARE_VKS_MCP_ALLOW_WRITE=create_namespace vmware-vks mcp
```

未提供的工具不会出现在客户端的工具列表里。取值错误或写工具名不存在时服务直接退出（exit 2）。
这只限制提供给客户端的工具，不是授权；账号本身仍应遵循最小权限。

详见 `skills/vmware-vks/references/setup-guide.md`（"Serving MCP over HTTPS"、"Limiting which tools are offered"）。

<a id="readme-troubleshooting"></a>

## 故障排查

### 故障排查

#### "VKS not compatible" 错误

必须在 vCenter 中启用 Workload Management。检查：vCenter UI -> Workload Management。需要 vSphere 8.x+ 且持有 Enterprise Plus 或 VCF 许可证。

#### 每个命令都报 "session is not authenticated"

密码没错。要么是会话过期（重跑命令即可，会新开会话），要么是账号没有 vSphere 角色，所以 vCenter 看不到它的会话，每个调用都失败。VKS 不会在连接时拒绝这种登录，因为只持有 Supervisor 命名空间权限的账号看起来一样、却能正常工作；错误会出现在第一个需要角色的调用上。在 vCenter 根文件夹上给账号分配一个角色（Read-Only 或更高）：Administration > Access Control > Global Permissions；命名空间和 TKC 操作还需要 Workload Management 权限。

#### 所有 REST 工具都返回 401

命名空间、存储策略、Supervisor 状态这些工具走的是 vSphere Automation REST API，
它认的是 `POST /api/session` 签发的会话 ID，**不是** pyVmomi 的 SOAP 会话 key ——
后者不是该 API 签发的，所以一律拒绝。遇到 401 会自动重新登录一次；若仍然 401，
检查中间的代理/网关是否把 `vmware-api-session-id` 头去掉了。账号缺少 Workload
Management 权限时返回的是 **403**，不是 401。

#### 创建命名空间失败，提示 "storage policy not found"

先列出策略：`vmware-vks supervisor storage-policies`，然后将 **Policy ID** 列的值（而非显示名称）作为 `--storage-policy` 传入。

#### TKC 集群卡在 "Creating" 阶段

检查 vCenter 中的 Supervisor 事件。常见原因：ESXi 主机资源不足、NSX-T 网络问题、或存储策略在目标数据存储上不可用。

#### Kubeconfig 获取失败

运行 vmware-vks 的机器必须能访问 Supervisor API 端点。检查 6443 端口的防火墙规则。

#### 扩容操作无效果

扩容前确认集群处于 "Running" 阶段。处于 "Creating" 或 "Updating" 阶段的集群会拒绝扩容操作。

#### 删除命名空间被意外拒绝

命名空间删除保护会在内部存在 TKC 集群时阻止删除。请先删除命名空间内的所有 TKC 集群，然后重试。

<a id="readme-companion-skills"></a>

## 配套 Skills

VMware Skills 家族包含 14 个 MCP Server 和共享 Policy 库。按职责选择工具： [vmware-skills](https://github.com/vmware-skills).

### 配套 Skills

> **VMware MCP Skills 系列。** 每个 Skill 负责一个独立领域 — 按需安装即可。

| Skill | 功能范围 | 工具数 | 安装 |
|-------|---------|:-----:|------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ 入口 | VM 生命周期、部署、Guest Ops、集群 | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | 只读监控、告警、事件、VM 信息 | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | 数据存储、iSCSI、vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX 网络：Segment、网关、NAT、IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW 微分段、安全组、Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops 指标、告警、容量规划 | 28 | `uv tool install vmware-aria` |

### 相关项目

| Skill | 范围 | 工具数 | 安装 |
|-------|------|:-----:|------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ 入口 | VM 生命周期、部署、Guest Ops、集群 | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | 只读监控、告警、事件、VM 信息 | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | 数据存储、iSCSI、vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX 网络：Segment、网关、NAT、IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW 微分段、安全组、Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops 指标、告警、容量规划 | 28 | `uv tool install vmware-aria` |

<!-- usage: remote-and-exposure -->

<a id="readme-reference"></a>

## 参考资料

变更记录： [RELEASE_NOTES.md](RELEASE_NOTES.md).

使用自己的模型和 Agent 宿主进行只读自测： [model-fit self-test](https://github.com/vmware-skills/marketplace/blob/main/MODEL_FIT_SELF_TEST.md).

### 升级到 3.0

3.0 改了两个 kubeconfig 工具的名字：`get_supervisor_kubeconfig` → `issue_supervisor_kubeconfig`、
`get_tkc_kubeconfig` → `issue_tkc_kubeconfig`。它们签发的是凭据（Supervisor bearer token），不是读取数据，而 `get_`
这个名字让 agent 以为只是读。参数、注解、安全门和 CLI 命令（`vmware-vks kubeconfig supervisor` / `kubeconfig get`）
都不变；调用旧名字的客户端必须改（已保存的 vmware-pilot 工作流由 Pilot ≥ 2.0.0 自动翻译）。`~/.vmware/rules.yaml` 里写旧名字的 deny 规则，在 vmware-policy ≥ 1.19.0 下照样生效。
详见 [RELEASE_NOTES.md](RELEASE_NOTES.md#v300--kubeconfig-tools-are-named-for-what-they-do)。

### 升级到 2.0

2.0 改了三个 VM Service 工具的名字：`list_vm_snapshots` → `list_vm_service_snapshots`、`list_vm_groups` →
`list_vm_service_groups`、`list_vm_network_interfaces` → `list_vm_service_network_interfaces`。原因是撞名：家族 2.0 里
vmware-aiops 和 vmware-monitor 把它们的 **vSphere** 虚拟机快照工具叫做 `list_vm_snapshots`，而这三个列的是 **Kubernetes VM
Service**（vm-operator）对象。**注意：** 如果 agent 提示词、脚本或已保存的工作流还在调用 `list_vm_snapshots`，并且同时加载了
vmware-aiops 或 vmware-monitor，现在拿到的是 vSphere 那个工具而不是这个——请改名。VKS 范围内针对 `list_vm_snapshots` 的
deny 规则现在也会匹配 aiops/monitor 的工具（vmware-policy ≥ 1.18 同时匹配新旧名；`vmware-audit policy` 会列出这类规则）。
参数、安全确认门和 CLI 命令都不变；完整对照表见 [RELEASE_NOTES.md](RELEASE_NOTES.md#v200--mcp-tools-renamed-verb-first)。
如果还有 2.0 之前的安装共用这个 `rules.yaml`，请把它的 vmware-policy 也升到 ≥ 1.18.2：它会按新旧两个名字匹配每条规则，一份文件两边都管用。

### 架构

```
用户（自然语言）
  ↓
AI Agent（Claude Code / Goose / Cursor）
  ↓ 读取 SKILL.md
  ↓
vmware-vks CLI  ─── 或 ───  vmware-vks MCP Server（stdio）
  │
  ├─ Layer 1: pyVmomi（/sdk）+ vSphere Automation REST（/api）
  │   Supervisor 状态、存储策略、命名空间 CRUD、VM 类、Harbor
  │   两套独立会话：SmartConnect 建立的 SOAP 会话，以及 POST /api/session
  │     单独签发的 REST 会话 ID（放在 vmware-api-session-id 头里）
  │
  └─ Layer 2: kubernetes client → Supervisor K8s API 端点
      TKC CR apply / get / delete（每次操作要求实际发现服务端提供
        cluster.x-k8s.io/v1beta1 或 v1beta2；发现失败即停止，不猜测或缓存回退版本）
      Kubeconfig 基于 POST /wcp/login 返回的 bearer token 在内存中构建
        （第三种凭据，同样不落盘）
  ↓
vCenter Server 8.x+（Workload Management 已启用）
  ↓
Supervisor 集群 → vSphere 命名空间 → TanzuKubernetesCluster
```

### 版本兼容性

| vSphere / VCF | 支持状态 | 说明 |
|---------|---------|------|
| 9.0 / 9.1 | ⚠ 9.1 外部局部实测 | 一份 9.1 实验室报告确认 Supervisor `/wcp/login` 位于 Supervisor 端点，但该环境的 guest TKC API 对本 skill 生成的 kubeconfig 返回 401（[#27](https://github.com/vmware-skills/VMware-VKS/issues/27)）。维护者尚未验证完整 Workload Management API；请运行 `check_vks_compatibility` 并反馈结果。 |
| 8.0+ | 完整支持 | Workload Management API 可用 |
| 7.x | 不支持 | WCP API 接口不同，请使用 vSphere 8.x |

VKS 3.8 增加了官方管理的链式升级和更细的节点放置能力（见[官方发布说明](https://blogs.vmware.com/cloud-foundation/2026/11/02/vks-3-8-advancing-ai-modern-workloads-private-cloud/)）。
本 skill 的 `upgrade_tkc_cluster` 仍只提交一个指定的 Kubernetes 版本，尚未验证或编排
3.8 的升级链及放置策略。新增的节点池摘要读取现有 Cluster 拓扑，不能据此宣称已通过
VCF 9.1/VKS 3.8 兼容性验证；真实 9.1/3.8 验证仍待完成。

<a id="readme-license"></a>

## 许可证

[MIT](LICENSE)

**以下为 main 上尚未发版的实现；已发布的 3.6.1 仍仅支持 v1beta1。**
Cluster API 创建支持在实际提供的 v1beta1 / v1beta2 接口中显式指定
`cluster_class` / `cluster_class_namespace`。MCP 预览和执行均要求 Supervisor
服务端 dry-run 校验通过；CLI 离线模板仍未验证。VCF 9.1 完整部署尚未经过真机验证。
详见[接口边界](skills/vmware-vks/references/capabilities.md#measured-cluster-api-schema-support)。
