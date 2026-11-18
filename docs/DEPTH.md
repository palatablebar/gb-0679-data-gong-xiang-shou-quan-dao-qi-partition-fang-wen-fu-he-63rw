# 深度登记 数据共享授权到期与分区访问复核

规模：中型。使用者：企业数据共享审计专员。核心规则：拒绝规则优先于允许，授权必须同时命中资源、分区和生效窗；显式拒绝不得被宽授权覆盖。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 访问采信 | 查看并运行访问采信 | `/api/accesses` | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py` |
| M2 资源映射 | 查看并运行资源映射 | `/api/resources` | `app/modules/m2_resources/rules.py` | `tests/test_m2.py` |
| M3 授权定版 | 查看并运行授权定版 | `/api/grants` | `app/modules/m3_grants/rules.py` | `tests/test_m3.py` |
| M4 冲突裁决 | 查看并运行冲突裁决 | `/api/conflicts` | `app/modules/m4_conflicts/rules.py` | `tests/test_m4.py` |
| M5 分区判定 | 查看并运行分区判定 | `/api/partitions` | `app/modules/m5_partitions/rules.py` | `tests/test_m5.py` |
| M6 访问对账 | 查看并运行访问对账 | `/api/audit` | `app/modules/m6_audit/rules.py` | `tests/test_m6.py` |
| M7 审计归档 | 查看并运行审计归档 | `/api/seals` | `app/modules/m7_seals/rules.py` | `tests/test_m7.py` |
| M8 共享摘要 | 为每位接收方整理可发布受控摘要 | `/api/shares`、`/api/runs/<id>/shares` | `app/modules/m8_shares/rules.py` | `tests/test_m8.py`、`tests/test_m8_api.py` |

## 共享摘要裁决

对外共享需要在「原值追查越权」与「最小必要」之间取舍，本项目交付可核对的受控摘要，而非整包阻断：

- 发布字段白名单保留可追查越权所需的原值；备注等受限字段默认隐藏。
- 隐藏不等于丢弃核对：`来源 = 范围内 + 范围外隐藏`，隐藏按范围原因分组输出行数、其中越权数与应/实合计，且范围内与隐藏两侧合计之和等于来源合计。
- 工作台展示每位接收方的隐藏数量、裁决依据（范围、最小化、对账、血缘、逐条解释）与未解释差异。
- 范围内仍有未解释的越权放行时整套摘要阻断：不出行、不占版本号，但在工作台保留完整计数供核对。
- 接收方与规范化策略共同构成请求指纹；重试同一指纹复用首次结果，不新增版本。阻断尝试同样幂等，先发布过的旧版继续可导出。
- 不完整或未发布的版本不能被导出，导出CSV末行为reconciliation汇总行。

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_accesses/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-07 | 上游结果空值 | 误报拒绝 | 隔离缺失 | `app/values.py` | `tests/test_m3.py::test_real_missing_decision_not_zero` |
| TRAP-19 | 允许与拒绝重叠 | 后写覆盖 | 拒绝优先且排序证据 | `app/modules/m4_conflicts/rules.py` | `tests/test_m4.py::test_deny_priority_counterexample` |
| TRAP-23 | 授权到期点 | 多放行一次 | 半开生效窗 | `app/modules/m3_grants/rules.py` | `tests/test_m3.py::test_expiry_endpoint_excluded` |
| TRAP-27 | 共享重试 | 每次请求都新增版本 | 请求指纹幂等落版 | `app/store.py` | `tests/test_m8_api.py::test_share_retry_reuses_attempt_and_version` |
| TRAP-28 | 隐藏记录 | 直接删行导致总数对不上 | 隐藏分组计数且两侧合计对平 | `app/modules/m8_shares/rules.py` | `tests/test_m8.py::test_published_summary_is_balanced_and_hides_notes` |
| TRAP-29 | 未解释越权 | 照常带出行或悄悄裁掉 | 整套阻断且不占版本号 | `app/modules/m8_shares/rules.py` | `tests/test_m8.py::test_unexplained_violation_blocks_whole_summary` |
| TRAP-30 | 发布失败后旧版 | 覆盖或删除旧摘要 | 旧版保留并可导出 | `app/store.py` | `tests/test_m8_api.py::test_blocked_attempt_stores_no_version_and_keeps_old` |
| TRAP-31 | 备注泄露 | 整包导出带出备注 | 白名单输出，备注默认隐藏 | `app/modules/m8_shares/rules.py` | `tests/test_m8_api.py::test_note_exposure_requires_opt_in` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。共享摘要只做接收方范围的字段裁剪，不替代接收方侧的访问控制；`include_note` 属敏感选项，应由审批流程控制勾选。
