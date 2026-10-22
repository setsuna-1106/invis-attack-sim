# InvisAttackSim

通过事件驱动的蒙特卡洛模拟，估计 BedWars 简化模型中固定队伍在不同时刻遭遇隐身攻击的事件密度（计入所有次数），并为后续真实对局比较提供数据。

## 当前状态

**M1 阶段已完成（2026-10-22）**，当前正式模型为 **m1.3**，已合并随时间变化的拆床成功率、无床后的隐身偏好和撤退概率调整。模型采用四队各一名玩家、一个中间岛的简化地图，提供全次数攻击统计、CSV / JSON 导出、图表和数值稳定性检查。阶段完成包括模型实现、行为测试、运行审计及一次真实记录的描述性对比；真实对局校准留待后续。开发进度见 [开发路线](docs/ROADMAP.md)，检查方法见 [验证说明](docs/VALIDATION.md)，版本变更见 [更新日志](docs/CHANGELOG.md)。

## 当前行为参数

令 `t` 为开局后秒数，`f = min(1, max(0, t/1800))`。拆床率在到达目标、完成拆床等待后的结算时刻取值：

```text
普通拆床率 = 0.08 * exp(-ln(8/3) * f)   0–30分钟：8% → 3%
隐身拆床率 = 0.60 - 0.20 * f            0–30分钟：60% → 40%
```

`normal_break_chance` / `normal_break_chance_end` 为普通指数曲线的起点/终点；`invis_break_chance` / `invis_break_chance_end` 为隐身线性曲线的起点/终点。相同端点表示常数；普通指数曲线允许两端同时为0。短于30分钟的观察窗口仍使用开局时间，不压缩曲线。

无床玩家将进攻中的隐身/普通基础权重比乘 `no_bed_invis_odds_multiplier`（默认2），保持收集基础权重和总进攻基础权重不变；之后仍排除资源不足或目标无效的行动并归一化。无床不会强制触发备药收集。

中间岛相遇的退出者或进攻失败者若无床，条件死亡率为 `death_chance * no_bed_failure_death_multiplier`，默认 `0.6 * 0.5 = 0.3`，其余70%撤退；有床时仍为60%死亡、40%撤退。这不改变追击时60%的击杀目标概率。规则对四队一致，由自身床状态触发，12分钟仅为分析分界。

历史m1.2参数可显式恢复为普通起点/终点均0.1、隐身起点/终点均0.6，两个无床倍率均1。旧配置缺少新增键时会使用当前默认值，复现旧结果应补齐这些参数；旧验收记录不代表当前模型。

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
configs/default.json       正式模型的默认参数
src/invis_attack_sim/
  config.py                参数校验、行动基础权重与拆床率曲线
  engine.py                事件驱动模拟、行为决策及状态约束
  results.py               全次数事件密度、区间概率与抽样稳定性
  output.py                CSV / JSON 导出与图表
  cli.py                   run / stability 命令
tests/                     行为规则、统计和命令行回归测试
docs/
  ROADMAP.md               阶段完成情况与后续方向
  VALIDATION.md            检查方法和分版本验收记录
  CHANGELOG.md             变更记录
pyproject.toml / uv.lock    包配置与锁定依赖
outputs/                   自动生成结果（Git 忽略）
.local/                    实验、分析脚本和内部资料（Git 忽略）
```

源码、运行配置和公开文档纳入版本控制。讨论记录、内部规格和详细规划保留在 `.local/`，公开文档不依赖这些文件。模型结果描述所设规则和参数下的行为，真实对局中的适用性需要另行验证。
