# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 共享摘要

`GET /api/shares` 使用最近批次返回工作台数据：批次内全部越权放行，以及按接收方分组的已发布版本、阻断尝试、隐藏计数、裁决依据与未解释差异。

`GET /api/runs/<id>/shares` 同上，但指定批次。

`POST /api/runs/<id>/shares` 生成某接收方的受控摘要并落版。请求体：

```json
{"recipient":"partner-a","policy":{"resources":["orders"],"partitions":["2026-10"],"principals":[],"include_note":false,"explanations":{"00001":"经批准的周期性对账访问"}}}
```

- `resources` 必填，取登记资源（orders/customers 或 订单/客户）。
- `partitions`、`principals` 为空表示该维度不限；分区必须是YYYY-MM。
- `include_note` 默认false：备注按最小必要隐藏，只输出隐藏计数。
- `explanations` 把范围内越权放行的事件号映射到裁决依据；引用范围外或非越权事件返回422。
- 范围内仍有未解释越权返回409，摘要不出行且不占版本号；响应仍含完整的工作台计数供核对。
- 同一`recipient`与同一规范化策略的请求指纹只落一条记录，重试返回首次结果；策略变化才产生新版本。
- 已发布版本号按接收方从1递增；阻断尝试不占版本号，且不影响历史已发布版本。

`GET /api/runs/<id>/shares/<recipient>/<version>` 返回指定已发布版本。

`GET /api/runs/<id>/shares/<recipient>/<version>/export` 导出该版本CSV。每行都带来源、发布、隐藏计数与对平标记，末尾一行kind=reconciliation 汇总；未发布版本返回404。
