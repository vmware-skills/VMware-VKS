<!-- mcp-name: io.github.zw008/vmware-vks -->
# VMware VKS

> **作者**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com
> 本项目由 VMware 工程师维护的社区项目，非 VMware 官方产品。
> VMware 官方开发者工具请访问 [developer.broadcom.com](https://developer.broadcom.com)。

[English](README.md) | [中文](README-CN.md)

MCP Skill + CLI，用于 VMware vSphere with Tanzu (VKS) 管理 — Supervisor 集群、vSphere 命名空间和 TanzuKubernetesCluster 生命周期。20 个 MCP 工具。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 配套 Skills

> **VMware MCP Skills 系列。** 每个 Skill 负责一个独立领域 — 按需安装即可。

| Skill | 功能范围 | 工具数 | 安装 |
|-------|---------|:-----:|------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ 入口 | VM 生命周期、部署、Guest Ops、集群 | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | 只读监控、告警、事件、VM 信息 | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | 数据存储、iSCSI、vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX 网络：Segment、网关、NAT、IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW 微分段、安全组、Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops 指标、告警、容量规划 | 28 | `uv tool install vmware-aria` |

## 前置要求

- **Python 3.10+** — `uv tool install` 所需
- **vSphere 8.0+** — Workload Management（Supervisor）API 要求 vSphere 8.x
- **Workload Management 已启用** — 至少一个计算集群已开启 WCP
- **许可证** — vSphere with Tanzu（Enterprise Plus 或 VMware Cloud Foundation）

配置完成后运行 `vmware-vks check` 验证所有要求是否满足。

## 快速开始

```bash
# 安装
uv tool install vmware-vks

# 配置
mkdir -p ~/.vmware-vks
cp config.example.yaml ~/.vmware-vks/config.yaml
# 编辑 config.yaml，填入 vCenter 主机和用户名

# 每个 target 一个密码环境变量，变量名由 config.yaml 里的 target 名派生：
# target "vcenter01"（config.example.yaml 自带的那个）-> VMWARE_VKS_VCENTER01_PASSWORD
echo "VMWARE_VKS_VCENTER01_PASSWORD=your_password" > ~/.vmware-vks/.env
chmod 600 ~/.vmware-vks/.env

# 验证
vmware-vks check

# 常用操作
vmware-vks supervisor status domain-c1
vmware-vks namespace list
vmware-vks tkc list
vmware-vks tkc create my-cluster -n dev --version v1.28.4+vmware.1 --vm-class best-effort-large
vmware-vks tkc create my-cluster -n dev --apply
```

## 离线 / 气隙环境安装（从源码）

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

## 常用工作流

### 部署新 TKC 集群

1. 检查兼容性 → `vmware-vks check`
2. 查看可用 K8s 版本 → `vmware-vks tkc versions -n dev`
3. 创建命名空间（如需）→ `vmware-vks namespace create dev --cluster domain-c1 --storage-policy <策略ID> --cpu 16000 --memory 32768 --apply`（策略 ID 通过 `vmware-vks supervisor storage-policies` 获取）
4. 创建 TKC 集群 → `vmware-vks tkc create dev-cluster -n dev --version v1.28.4+vmware.1 --control-plane 1 --workers 3 --vm-class best-effort-large --apply`
5. 获取 kubeconfig → `vmware-vks kubeconfig get dev-cluster -n dev`

### 扩容工作节点（压测场景）

1. 查看当前状态 → `vmware-vks tkc get dev-cluster -n dev`
2. 扩容 → `vmware-vks tkc scale dev-cluster -n dev --workers 6`
3. 监控进度 → `vmware-vks tkc get dev-cluster -n dev`（观察 phase）
4. 测试结束后缩容

### 命名空间资源管理

1. 列出命名空间 → `vmware-vks namespace list`
2. 查看使用情况 → `vmware-vks storage -n dev`
3. 更新配额 → `vmware-vks namespace update dev --cpu 32000 --memory 65536`

## 工具参考（20 个工具）

### Supervisor

| 工具 | 描述 | 类型 |
|------|------|------|
| `check_vks_compatibility` | vCenter 版本检查 + WCP 状态 | 只读 |
| `get_supervisor_status` | Supervisor 集群状态和 K8s API 端点 | 只读 |
| `list_supervisor_storage_policies` | vCenter 存储策略列表（策略 ID、名称、描述） | 只读 |

### 命名空间

| 工具 | 描述 | 类型 |
|------|------|------|
| `list_namespaces` | 所有 vSphere 命名空间及状态 | 只读 |
| `get_namespace` | 命名空间详情（配额、存储、角色） | 只读 |
| `create_namespace` | 创建命名空间（默认 dry-run 预览） | 写操作 |
| `update_namespace` | 修改配额和存储策略 | 写操作 |
| `delete_namespace` | 删除（含 TKC 保护，存在集群时拒绝） | 写操作 |
| `list_vm_classes` | TKC 节点规格可用的 VM 类 | 只读 |

### TKC

| 工具 | 描述 | 类型 |
|------|------|------|
| `list_tkc_clusters` | TanzuKubernetesCluster 列表及状态 | 只读 |
| `get_tkc_cluster` | 集群详情（节点、健康、条件） | 只读 |
| `get_tkc_available_versions` | Supervisor 上支持的 K8s 版本 | 只读 |
| `create_tkc_cluster` | 创建 TKC（YAML 计划 + 默认 dry-run） | 写操作 |
| `scale_tkc_cluster` | 调整工作节点数量 | 写操作 |
| `upgrade_tkc_cluster` | 升级 K8s 版本 | 写操作 |
| `delete_tkc_cluster` | 删除（含工作负载保护） | 写操作 |

### 访问

| 工具 | 描述 | 类型 |
|------|------|------|
| `get_supervisor_kubeconfig` | Supervisor kubeconfig YAML | 只读 |
| `get_tkc_kubeconfig` | TKC kubeconfig（标准输出或文件） | 只读 |
| `get_harbor_info` | 内置 Harbor 仓库信息（ID、集群、版本、URL、健康状态、已用存储） | 只读 |
| `list_namespace_storage_usage` | PVC 列表和容量统计 | 只读 |

## 架构

```
用户（自然语言）
  ↓
AI Agent（Claude Code / Goose / Cursor）
  ↓ 读取 SKILL.md
  ↓
vmware-vks CLI  ─── 或 ───  vmware-vks MCP Server（stdio）
  │
  ├─ Layer 1: pyVmomi → vCenter REST API
  │   Supervisor 状态、存储策略、命名空间 CRUD、VM 类、Harbor
  │
  └─ Layer 2: kubernetes client → Supervisor K8s API 端点
      TKC CR apply / get / delete（cluster.x-k8s.io API 版本运行时自动探测：
        Supervisor 支持 v1 时优先选 v1，否则回退 v1beta1（vSphere 8.0））
      Kubeconfig 基于 Layer 1 会话令牌在内存中构建（不落盘）
  ↓
vCenter Server 8.x+（Workload Management 已启用）
  ↓
Supervisor 集群 → vSphere 命名空间 → TanzuKubernetesCluster
```

## CLI 参考

```bash
# 环境检查
vmware-vks check

# Supervisor
vmware-vks supervisor status <集群ID>
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
vmware-vks kubeconfig supervisor -n <命名空间>
vmware-vks kubeconfig get <集群名称> -n <命名空间> [-o <文件路径>]

# Harbor 和存储
vmware-vks harbor
vmware-vks storage -n <命名空间>
```

## MCP Server 配置

**v1.5.15+ 推荐方式**：完成 `uv tool install vmware-vks` 后，**一条命令启动 MCP**：

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
uvx --from vmware-vks vmware-vks mcp

# 旧 entry point（仍可用，向后兼容）
vmware-vks-mcp
```

> **公司 TLS 代理网络下？** uvx 可能报 `invalid peer certificate: UnknownIssuer`。
> 推荐使用上面的 `vmware-vks mcp`（无需联网），或 `export UV_NATIVE_TLS=true`。

</details>

## 安全性

| 特性 | 说明 |
|------|------|
| 以只读为主 | 20 个工具中 13 个为只读 |
| 默认 dry-run | `create_namespace`、`create_tkc_cluster`、`delete_namespace`、`delete_tkc_cluster` 均默认 `dry_run=True` |
| TKC 保护 | `delete_namespace` 在命名空间内存在 TKC 集群时拒绝执行 |
| 工作负载保护 | `delete_tkc_cluster` 在 Deployment/StatefulSet 运行时拒绝执行 |
| 凭据安全 | 密码仅从环境变量（`.env` 文件）加载，不写入 `config.yaml` |
| Kubeconfig 内存构建 | Supervisor/TKC kubeconfig（含 vCenter 会话 bearer token）以内存 dict 形式构建并通过 `load_kube_config_from_dict()` 加载 — 不会写入磁盘临时文件（v1.5.18+） |
| 审计日志 | 所有写操作记录到 `~/.vmware-vks/audit.log` |
| stdio 传输 | 无网络监听端口；MCP 通过 stdio 运行 |

## 故障排查

### "VKS not compatible" 错误

必须在 vCenter 中启用 Workload Management。检查：vCenter UI -> Workload Management。需要 vSphere 8.x+ 且持有 Enterprise Plus 或 VCF 许可证。

### 创建命名空间失败，提示 "storage policy not found"

先列出策略：`vmware-vks supervisor storage-policies`，然后将 **Policy ID** 列的值（而非显示名称）作为 `--storage-policy` 传入。

### TKC 集群卡在 "Creating" 阶段

检查 vCenter 中的 Supervisor 事件。常见原因：ESXi 主机资源不足、NSX-T 网络问题、或存储策略在目标数据存储上不可用。

### Kubeconfig 获取失败

运行 vmware-vks 的机器必须能访问 Supervisor API 端点。检查 6443 端口的防火墙规则。

### 扩容操作无效果

扩容前确认集群处于 "Running" 阶段。处于 "Creating" 或 "Updating" 阶段的集群会拒绝扩容操作。

### 删除命名空间被意外拒绝

命名空间删除保护会在内部存在 TKC 集群时阻止删除。请先删除命名空间内的所有 TKC 集群，然后重试。

## 版本兼容性

| vSphere / VCF | 支持状态 | 说明 |
|---------|---------|------|
| 9.0 / 9.1 | ⚠ 尚未验证 | vSphere 9 的 Workload Management（Supervisor / WCP）API 维护者尚未实测。现有 vSphere 8.x 代码路径理论上可用，但在完成实验室验证之前无任何保证 — 基本 CRUD 大概率正常工作，边界场景可能需要测试。如在 VCF 9 上运行，欢迎提交 issue 并附带 `check_vks_compatibility` 输出。 |
| 8.0+ | 完整支持 | Workload Management API 可用 |
| 7.x | 不支持 | WCP API 接口不同，请使用 vSphere 8.x |

## 相关项目

| Skill | 范围 | 工具数 | 安装 |
|-------|------|:-----:|------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ 入口 | VM 生命周期、部署、Guest Ops、集群 | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | 只读监控、告警、事件、VM 信息 | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | 数据存储、iSCSI、vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX 网络：Segment、网关、NAT、IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW 微分段、安全组、Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops 指标、告警、容量规划 | 28 | `uv tool install vmware-aria` |

## 许可证

[MIT](LICENSE)
