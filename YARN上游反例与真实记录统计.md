# YARN 上游反例与真实预约统计

2026-11-09

## 上游最小反例

补丁只给 Hadoop 的 TestCapacityOverTimePolicy 新增一个 JUnit 测试。沿用原测试的 24 小时窗口和 25% 平均配额，在总容量 100 MiB 的计划中依次提交两条预约。

| 预约 | 区间 | 用量 |
| --- | --- | ---: |
| 历史 | 第 0–12 小时 | 40 MiB |
| 新请求 | 第 12–36 小时 | 20 MiB |

历史预约单独符合配额。提交新请求后，第 0–24 小时内累计用量为 720 MiB·小时，超过 600 MiB·小时上限。旧策略在相邻两个有效右端采样点只得到 480 MiB·小时，接纳了请求。测试断言 YARN 应抛出 PlanningQuotaException。

| 策略源码 | 测试结果 |
| --- | --- |
| Hadoop 主线 d34c109a87ca5d219633544d5baee2b557db1247 | 预期拒绝，实际接纳，断言失败 |
| YARN-12022 补丁 f99af959fd4e26cf0b359e6f341d560761d14101 | 同样实际接纳，断言失败 |
| 保留瞬时检查、替换为单内存精确扫描的本地对照 | 正确拒绝，断言通过 |

补丁通过 Git 应用检查。测试编译固定源码并链接 Hadoop 3.5.0 的其余依赖。对照实现用于确认该反例能区分采样与精确判定，覆盖范围为单内存。复跑方法见运行脚本。补丁尚未提交上游。

[单文件 JUnit 补丁](results/yarn-upstream-repro/yarn-quota-missed-peak.patch) · [复跑脚本](results/yarn-upstream-repro/run.ps1) · [主线日志](results/yarn-upstream-repro/trunk.log) · [补丁版日志](results/yarn-upstream-repro/patch.log) · [精确对照日志](results/yarn-upstream-repro/exact-control.log) · [YARN-12022](https://github.com/apache/hadoop/pull/8815)

## 真实预约统计需要的记录

本轮没有可用的生产准入记录。YARN 的预约列表可以提供当前已接纳的资源分段，常规审计日志可以区分操作和成功失败。统计漏放、误拒还需要被拒的候选分段和校验前的历史快照。公开 Borg 轨迹含作业及共享资源预约，但使用的是 Borg，缺少 YARN 同一时刻的配额和准入决定。Rayon 使用真实作业分布构造合成负载，为第二条实验路线提供了先例。[YARN 预约列表接口](https://apache.github.io/hadoop/hadoop-yarn/hadoop-yarn-site/ResourceManagerRest.html#Cluster_Reservation_API_List) · [审计日志接口](https://hadoop.apache.org/docs/r2.8.3/hadoop-yarn/hadoop-yarn-server/hadoop-yarn-server-resourcemanager/apidocs/org/apache/hadoop/yarn/server/resourcemanager/RMAuditLogger.html) · [Borg 轨迹](https://github.com/google/cluster-data/blob/master/ClusterData2019.md) · [Rayon 负载方法](https://www.microsoft.com/en-us/research/wp-content/uploads/2013/10/rayon.pdf)

建议在原积分校验处、状态写回前记录每次尝试。替换的首次回调保留原有分段；同一事务的第二次回调单独标记，导出时只纳入首次判定。周期预约按实际校验范围展开。记录可用匿名序号和相对时间，资源数据保持原精度。

统计脚本读取 UTF-8 JSONL。首行包含 kind（真实采集为 yarn_capture，构造为 synthetic）和 source。每次尝试一行，字段如下。

| 字段 | 含义 |
| --- | --- |
| seq、operation、legacy_outcome | 递增序号、submit/replace/cancel、accept/integral_reject/other_reject/not_checked |
| window_ms、quota_mib_ms | 窗口宽度、内存积分配额 |
| first_ms、last_ms | 被检查的窗口右端点范围 |
| snapshot_complete、coverage_start_ms、coverage_end_ms | 采集端确认的历史覆盖范围，必须包括从 first_ms−window_ms 到 last_ms |
| history、old、candidate | 按 [开始毫秒, 结束毫秒, MiB] 表示的分段。history 含旧预约，替换时通过 old 扣除 |

integral_reject 表示确认为内存积分导致的拒绝。瞬时超额、CPU 或其他资源拒绝，以及原因无法区分的拒绝从内存误判分母中排除。脚本按同一旧状态逐条比较实际判定和精确判定，输出漏放、误拒以及历史变化点的 P50/P95。精确策略若改变接纳结果并继续运行，后续计划也会改变；那属于另一种连续反事实回放。

运行 python -B sliding-window-quota/analyze_yarn_reservation_records.py 路径。现有自测覆盖漏放、误拒、替换旧分段与不完整历史。[统计脚本](analyze_yarn_reservation_records.py) · [构造自测输出](results/yarn-upstream-repro/synthetic-analysis.json)

## 论文的下一步

1. 把精确扫描做成可审阅的策略实现，明确多资源比较，覆盖替换的两次回调、周期展开和数值边界。这个阶段的验收是最小反例转绿、上游预约回归通过、判定改变有可解释的窗口见证。
2. 用同一份请求流比较原策略和精确版，分别报告一致判定与改变判定的请求，测到计划写回结束。若能取得集群准入快照，计算真实频率；否则用可公开复跑的真实作业轨迹派生预约流，报告实验负载上的误判与成本。

目前足以写技术报告和上游问题材料。短论文需要完成以上两步的证据闭环。SPE 投稿还应明确替换后的应用边界与上游可复现性。
