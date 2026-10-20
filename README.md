# InvisAttackSim

通过数学建模模拟 BedWars 对局中的隐身药水使用情况，并与真实对局数据比较，为对局中的隐身防范提供依据。

## 当前状态

项目已建立 Python 开发骨架；模拟规则、数据来源、输入格式和评价指标尚待明确，目前没有实现模拟算法。

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
docs/ROADMAP.md        待确认事项与开发路线
docs/CHANGELOG.md      变更记录
```

## 模型范围

具体游戏规则、观测数据及其来源需要先确认，再据此确定模拟假设和实现范围。项目不会把未经核实的规则或数据当成事实。
