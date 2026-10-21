# InvisAttackSim

通过数学建模模拟 BedWars 对局中的隐身药水使用情况，并与真实对局数据比较，为对局中的隐身防范提供依据。

## 当前状态

项目已建立 Python 开发骨架，下一阶段为模拟实现与验证，目前尚无模拟结果。开发阶段见 [开发路线](docs/ROADMAP.md)。

## 开发环境

- Python 3.11（由 `.python-version` 固定）
- [uv](https://docs.astral.sh/uv/)

创建或同步环境：

```bash
uv sync --no-editable
```

在当前 macOS / Python 3.11 环境中，Python 会跳过隐藏 `.venv` 下的 editable 安装路径，因此使用普通 wheel 安装。修改源码后重新运行上述命令；调用项目环境中的 Python 时使用 `uv run --no-sync ...`，避免 uv 将环境切回 editable 安装。

## 目录结构

```text
src/invis_attack_sim/  Python 源码包
docs/ROADMAP.md        公开开发路线
docs/CHANGELOG.md      变更记录
.local/               本地讨论、内部规格与详细规划（Git 忽略）
```

源码、项目配置和公开文档纳入版本控制。讨论记录、内部规格和详细规划统一保存在 `.local/`，不纳入 GitHub 仓库；公开文档不依赖这些本地文件。

## 模型范围

模型描述所设规则与参数下的模拟结果；真实对局中的适用性需要另行验证。项目区分建模假设和已核验的游戏事实。
