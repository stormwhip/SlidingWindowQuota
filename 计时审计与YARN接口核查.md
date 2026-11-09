# C++ 计时审计与 YARN 接口核查

## 计时边界

原版精确上限实验在开始回放前读取并解析整个输入文件。计时区间内使用内存中的请求，不读取文件或标准输入。每个请求记三个时间点：

| 时间段 | 计入的工作 |
| --- | --- |
| 开始到判定完成 | 计算分段、查询索引、求上限或判定是否超额 |
| 判定完成到请求完成 | 接纳或取消后的索引维护，包括其中的节点分配和释放 |
| 请求完成后 | 对照参考答案、保存计时；本轮请求全部完成后，抽查状态并写 CSV |

报表用两个计时间隔之和作为请求总时间。索引构建另计，从公共初始点及斜率事件准备完成后开始。它没有混入请求总时间。程序使用单调时钟测经过时间。结果衡量的是进程内的查询和更新。

[输入读完再回放](native_replay.cpp#L681) · [请求的三次读钟](native_replay.cpp#L627) · [计时后核对和写出](native_replay.cpp#L645) · [汇总公式](summarize_native_replay.py#L35)

## YARN 现有流程

核查版本为 Apache Hadoop 提交 1fdf777d777d74e9de04961e9a657e61107eadec。客户端提交预约定义后，ResourceManager 先由预约代理尝试安排，再由计划调用策略校验。新增和修改的成功响应没有精确需求上限或限制窗口字段。失败通过异常报告。现有业务接口适合先评估**只判断是否超额**的算法；精确上限可作为另行验证的新反馈能力。[客户端提交实现](https://github.com/apache/hadoop/blob/1fdf777d777d74e9de04961e9a657e61107eadec/hadoop-yarn-project/hadoop-yarn/hadoop-yarn-server/hadoop-yarn-server-resourcemanager/src/main/java/org/apache/hadoop/yarn/server/resourcemanager/ClientRMService.java#L1249) · [计划调用策略](https://github.com/apache/hadoop/blob/1fdf777d777d74e9de04961e9a657e61107eadec/hadoop-yarn-project/hadoop-yarn/hadoop-yarn-server/hadoop-yarn-server-resourcemanager/src/main/java/org/apache/hadoop/yarn/server/resourcemanager/reservation/InMemoryPlan.java#L318) · [提交响应定义](https://github.com/apache/hadoop/blob/1fdf777d777d74e9de04961e9a657e61107eadec/hadoop-yarn-project/hadoop-yarn/hadoop-yarn-api/src/main/java/org/apache/hadoop/yarn/api/protocolrecords/ReservationSubmissionResponse.java)

当前原型覆盖固定窗口、单资源配额。YARN 的实际策略还包含瞬时限制、用户与队列状态，并可能校验多段或周期预约。正式对照前须固定上游版本和修复状态，逐项核对窗口裁剪、取整、周期和更新语义。[YARN 预约系统](https://apache.github.io/hadoop/hadoop-yarn/hadoop-yarn-site/ReservationSystem.html) · [YARN-12022](https://issues.apache.org/jira/browse/YARN-12022)

## 同一输入的只判定复测

两种原生索引都只返回通过或拒绝。扫描版可在发现第一个超额点时停止，公切线树也可遇到超额子树时停止。仍计入接纳后的更新，仍不计入输入输出。输入、重复次数和计时位置与原生上限实验一致。2,560 个请求全部符合参考判定；两种模式的 38,400 条测量行逐键对应。

较大规模的宽区间结果，单位为微秒：

| 拒绝率 | 扫描均值 | 公切线树均值 | 扫描 P95 | 公切线树 P95 |
| ---: | ---: | ---: | ---: | ---: |
| 0% | 61.19 | 28.05 | 130.64 | 39.81 |
| 50% | 51.27 | 17.13 | 100.02 | 38.20 |

新版均值约快 2.2 倍和 3.0 倍，两个种子的均值与 P95 方向一致。短区间和小规模宽区间仍由扫描胜出。这是单资源模型的只判定复测，尚未运行 YARN 的 Java 预约路径。[逐次计时](results/native-decision/samples.csv) · [汇总](results/native-decision/summary.csv)

复跑可使用[C++ 原生实验的命令](C++原生复测.md)。在可执行文件命令末尾加 **--decision-only**，把计时输出写到新目录，再按同样方式生成汇总。

## 可公开回放的数据

目前查到的公开资料能支持不同层次的实验。Google Borg 2019 提供作业、资源请求与共享资源预约信息；阿里 GPU 2020 提供作业提交、完成、状态和任务资源需求。两者可用于构造有实测时间和规模分布的压力负载。查阅的公开字段未提供直接回放 YARN 滑窗配额判定所需的一整套信息，不能从作业失败推断配额拒绝。Rayon 论文附录的 7,200 条调度模拟也来自 SWIM 生成负载。[Borg 数据说明](https://github.com/google/cluster-data/blob/master/ClusterData2019.md) · [阿里数据字典](https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2020) · [Rayon 技术报告](https://issues.apache.org/jira/secure/attachment/12635661/techreport.pdf)

真实预约回放至少需要按提交顺序记录新增、修改、取消，记录新旧资源分段、用户与队列的配额及窗口长度、初始预约状态、接纳决定和失败原因。现有源码的成功或失败审计调用能识别部分请求与结果，字段仍需在校验入口补齐。下一步先用 YARN 的公开预约测试建立 Java 对照，隔离计时的滑窗检查并核对语义。拿到满足上述字段要求的预约记录后，再测实际请求会覆盖多少变化点和索引复用次数。
