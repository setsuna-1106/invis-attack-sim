# InvisAttackSim

通过事件驱动的蒙特卡洛模拟，估计 BedWars 简化模型中固定队伍首次遭遇隐身攻击的时间分布，并为后续真实对局比较提供数据。

## 当前状态

M1 已实现配置驱动的四队模拟、首事件统计、CSV / JSON 导出、图表和数值稳定性检查。模型采用四队各一名玩家、一个中间岛的简化地图；结果尚未经真实对局校准。开发进度见 [开发路线](docs/ROADMAP.md)，检查方法见 [验证说明](docs/VALIDATION.md)。

## 运行

需要 Python 3.11 和 [uv](https://docs.astral.sh/uv/)。在项目根目录执行：

```bash
uv sync --no-editable
uv run --no-sync invis-attack-sim run --config configs/default.json --output outputs/m1
```

默认运行 10,000 局，种子 42。可以通过 JSON 修改参数，或覆盖局数和种子：

```bash
uv run --no-sync python -m invis_attack_sim run --trials 1000 --seed 43 --output outputs/quick
```

`--audit` 会在每个事件后检查库存、行动和玩家状态约束。5 秒只用于结果分箱，模拟按事件时间推进。当前 macOS 环境使用普通 wheel 安装；修改源码后需重新 `uv sync --no-editable`，运行时保留 `--no-sync`。

每次运行导出：

| 文件 | 内容 |
| --- | --- |
| `density.png` / `density.svg` | 首次攻击概率密度与累计概率 |
| `histogram.csv` | 箱起止时间、次数、概率及每秒密度 |
| `trials.csv` | 逐局首次事件时间、停止时间、原因及攻击来源 |
| `config.json` | 本次完整配置 |
| `summary.json` | 样本数、种子、模型版本、源码指纹、运行时间及未遭遇概率 |

CSV 中时间单位为秒。所有对局都保留在分母中。密度积分等于有效统计区间内发生首次攻击的概率；它与未遭遇概率之和为 1。未遭遇也包括先被普通进攻拆床、对局提前结束等情况，不能直接解释为安全概率。仅记录我方床仍存在时的首次有效隐身越界，观察上限为 30 分钟。

## 验证

```bash
uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync invis-attack-sim stability --config configs/default.json --output outputs/stability --audit
```

稳定性命令默认分别运行 10,000 / 40,000 / 160,000 局，种子为 42 / 43 / 44，保存每组完整输出、累计概率对比图和 `stability.json`。最后两组的经验累计分布差异超过预设抽样误差界时，命令返回非零退出码。该检查衡量数值抽样稳定性，不能验证模型与真实比赛的一致性。

## 目录结构

```text
configs/              可复用的运行配置
src/invis_attack_sim/ 模拟、统计、导出及命令行
tests/                行为规则与输出检查
docs/                 公开开发路线、验证说明与 CHANGELOG
outputs/              自动生成结果（Git 忽略）
.local/               本地讨论、内部规格与详细规划（Git 忽略）
```

源码、运行配置和公开文档纳入版本控制。讨论记录、内部规格和详细规划保留在 `.local/`，公开文档不依赖这些文件。模型结果描述所设规则和参数下的行为，真实对局中的适用性需要另行验证。
