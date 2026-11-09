# YARN 原策略的 Java 对照

## 运行对象

本地 Maven 对照直接编译 Apache Hadoop 固定提交的 **CapacityOverTimePolicy.java** 和公开测试 **TestCapacityOverTimePolicy.java**。其余 ResourceManager 类和测试辅助类使用 Hadoop 3.5.0 发布包。本轮使用 Maven 3.9.16、JDK 26.0.1，测试代码编译目标为 Java 17，时区为 UTC。这个装置调用了原策略和真实的内存预约计划，未启动完整 ResourceManager，也没有访问外部集群。

主线策略固定为 1fdf777d777d74e9de04961e9a657e61107eadec，修复版为 f99af959fd4e26cf0b359e6f341d560761d14101。同一组新增回归测试分别链接两版策略。[修复提案与测试说明](https://github.com/apache/hadoop/pull/8815) · [本地 Maven 配置](yarn-java/pom.xml)

## 语义核查

修复版通过了 33 个公开测试和 2 个本地测试。对主线策略运行修复版新增的三个用例时，三者均复现了意外的配额拒绝：

| 回归用例 | 主线策略 | 修复版 |
| --- | --- | --- |
| 同一用户的另一项预约在检查窗口末端仍在运行 | 抛出配额异常 | 接纳 |
| 周期预约跨过周期边界 | 抛出配额异常 | 接纳 |
| 配额内修改预约 | 抛出配额异常 | 接纳；超额修改仍拒绝 |

另用一个空计划分离了取整语义。窗口为 4 秒，平均内存配额为 10 MiB。申请 41 MiB、持续 1 秒时，精确峰值为 41 MiB·秒，高于配额 40 MiB·秒；**修复版策略仍接纳**。申请 42 MiB、持续 1 秒时，策略拒绝。两个请求的瞬时用量都在配置范围内。这个结果与该路径先把累计积分除以窗口长度并取整，再取差的代码一致；当存在多个历史区段时，取整后前缀相减与对窗口积分单独取整也不等价。

因此，当前公切线树求的是精确积分配额。直接替换 YARN 的校验会改变部分临界请求的接纳决定。后续应分别记录现有策略与精确配额判定，并在性能实验中对齐所比较的语义。[临界值用例](yarn-java/src/test/java/org/apache/hadoop/yarn/server/resourcemanager/reservation/PolicyProbeTest.java#L59) · [固定修复版源码](yarn-java/upstream/patch/CapacityOverTimePolicy.java#L217)

## 策略入口计时

在已构造的内存计划上，交替验证可接纳和超额的固定候选预约。每种背景规模和结果先热身 80 次，再记录 120 次，共 480 条。两次读钟只包围 **CapacityOverTimePolicy.validate** 及失败异常的接收；包括实际策略内部的瞬时检查、滑窗计算、内存分配与异常构造。计划和请求的构造、结果断言、数据整理及 CSV 写出均在区间外。测试日志关闭，测量进程固定到逻辑处理器 2。

| 背景资源分段数 | 接纳中位耗时 | 拒绝中位耗时 |
| ---: | ---: | ---: |
| 128 | 0.261 ms | 0.310 ms |
| 1,024 | 1.697 ms | 1.575 ms |

这是原策略入口在两组合成状态上的基准。它包含 C++ 原型尚未执行的 YARN 检查，并沿用 YARN 自身的取整规则。两边的微秒数字不能当作同任务加速比。

## 下一步与复跑

下一步用同一份 Java 预约状态实现一个**逐窗口精确积分的参考检查器**，先复现新增、修改、跨周期和取整临界点的结果，再区分两类任务：维持 YARN 现有接纳语义的优化，以及采用精确配额规则时的行为差异。性能对照应在同一 Java 入口、同一语义和同一请求流上进行。

在工作区运行 **& 'sliding-window-quota/yarn-java/run_checks.ps1' -OutputDirectory 'sliding-window-quota/results/yarn-policy-repeat'**，使用新输出目录。脚本只有在修复版全部通过、主线三个回归均抛出配额异常时才报告完成。

[修复版日志](results/yarn-policy/patch.log) · [主线回归结果](results/yarn-policy/trunk-regressions.xml) · [逐次计时](results/yarn-policy/validate-samples.csv) · [计时汇总](results/yarn-policy/summary.csv)
