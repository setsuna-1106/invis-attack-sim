# InvisAttackSim

通过事件驱动的蒙特卡洛模拟，估计 BedWars 简化模型中固定队伍在不同时刻遭遇隐身攻击的事件密度（计入所有次数），并为后续真实对局比较提供数据。

## 当前状态

M1 已实现配置驱动的四队模拟、全次数攻击统计、CSV / JSON 导出、图表和数值稳定性检查。模型采用四队各一名玩家、一个中间岛的简化地图；结果尚未经真实对局校准。开发进度见 [开发路线](docs/ROADMAP.md)，检查方法见 [验证说明](docs/VALIDATION.md)。

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

`--audit` 会在每个事件后检查库存、行动和玩家状态约束。5 秒只用于结果分箱，模拟按事件时间推进。当前 macOS 环境使用普通 wheel 安装；修改源码后使用 `uv sync --no-editable --reinstall-package invis-attack-sim` 确保源码重新安装，运行时保留 `--no-sync`。

每次运行导出：

| 文件 | 内容 |
| --- | --- |
| `attack_density.png` / `attack_density.svg` | 所有隐身攻击的时间事件密度，单位为次 /（局·秒） |
| `interval_probability.png` / `interval_probability.svg` | 每个分箱内至少遭遇一次隐身攻击的概率 |
| `density.png` / `density.svg` | 攻击事件密度与区间概率组合图 |
| `histogram.csv` | 箱起止时间、攻击总数、每局平均次数、每秒密度及至少一次攻击概率 |
| `trials.csv` | 每局攻击总数、首末攻击时间、停止时间及原因 |
| `attacks.csv` | 每次攻击的对局编号、局内序号、时刻及攻击来源 |
| `config.json` | 本次完整配置 |
| `summary.json` | 样本数、种子、模型版本、源码指纹、运行时间及未遭遇概率 |

CSV 中时间单位为秒。每次有效隐身越界都计一次，首次攻击后继续模拟。统计截至我方床被摧毁、我方淘汰、对局结束或开局 30 分钟，所有对局始终保留在分母中，包括提前终止的对局。

设分箱宽度为 Δt、总对局数为 N，则：

```text
事件密度 = 该箱内所有攻击次数 / (N × Δt)
区间概率 = 该箱内至少遭遇一次攻击的对局数 / N
```

事件密度曲线的面积等于**每局平均攻击次数**，可以大于 1。区间概率将同一局在一个箱内的多次攻击只算一次，取值在 0–1 之间。它们都不按攻击序号筛选，也不按有攻击的对局重新归一化。未遭遇概率另行记录，包含提前拆床或对局结束的零攻击对局，不能直接解释为安全概率。

## 验证

```bash
uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync invis-attack-sim stability --config configs/default.json --output outputs/stability --audit
```

稳定性命令默认分别运行 10,000 / 40,000 / 160,000 局，种子为 42 / 43 / 44，保存每组完整输出、每局平均累计攻击次数对比图和 `stability.json`。最后两组的平均累计次数曲线在各分箱边界的最大差异，与按整局重采样得到的近似 95% bootstrap 误差带比较（默认 400 次，可用 `--bootstrap-repetitions` 调整）；超过误差带时命令返回非零退出码。同一局的攻击事件一同重采样。该检查衡量数值抽样稳定性，不能验证模型与真实比赛的一致性。

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
