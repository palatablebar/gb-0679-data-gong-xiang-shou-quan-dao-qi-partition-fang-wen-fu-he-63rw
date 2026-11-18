# 数据共享授权到期与分区访问复核

供企业数据共享审计专员核对原始记录、处理差异并封存证据。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

打开 http://localhost:18248 。首次进入自动运行仓内脏样例。工具条可重跑、切换样例或上传CSV。SQLite保存在命名卷records中，重建服务保留批次。

## 模块

- 1. 访问采信：`/accesses`，`/api/accesses`。
- 2. 资源映射：`/resources`，`/api/resources`。
- 3. 授权定版：`/grants`，`/api/grants`。
- 4. 冲突裁决：`/conflicts`，`/api/conflicts`。
- 5. 分区判定：`/partitions`，`/api/partitions`。
- 6. 访问对账：`/audit`，`/api/audit`。
- 7. 审计归档：`/seals`，`/api/seals`。
- 8. 共享摘要：`/shares`，`/api/shares`。

## 核心规则

拒绝规则优先于允许，授权必须同时命中资源、分区和生效窗；显式拒绝不得被宽授权覆盖。

## 对外共享摘要

接收方需要按原值追查越权，但共享规则只允许最小必要信息。`/shares` 工作台按接收方把批次整理成可发布摘要：

- 范围内记录按外部字段白名单输出原值；备注等受限字段默认隐藏，只报告隐藏行数与字段计数。
- 发布误导可比对：`来源 = 范围内 + 范围外隐藏`，且范围内与隐藏两侧的应/实合计相加等于来源合计，隐藏部分仍能核对。
- 工作台列出每位接收方的隐藏数量、分组原因、裁决依据与未解释差异。
- 范围内仍有未解释的「越权放行」时，整套摘要阻断，不出行、不落版本；旧版本继续保留并可导出。
- 同一接收方的同一请求指纹只落一次，重试不新增版本；请求变化才产生新版本。

`POST /api/runs/<id>/shares` 接收`recipient`与`policy`（`resources`、`partitions`、`principals`、`include_note`、`explanations`）；`GET /api/runs/<id>/shares`返回工作台数据；`GET /api/runs/<id>/shares/<recipient>/<version>/export`导出已发布版本的CSV。阻断返回409。

## 演示

1. 打开首页，核对当次计算、差异和隔离原因。
2. 切换模块查看源行及裁决依据。
3. 重跑相同样例，批次号与行数保持一致。
4. 复核后导出一份证据CSV。版本已变化的复核提交返回409。
5. 打开共享摘要，选择接收方与范围生成受控摘要；未解释越权会被阻断，补齐裁决依据后重试仍不新增冗余版本。

## 输入与输出

输入列见 `docs/DATA-DICTIONARY.md`。项目包含dirty.csv、clean.csv、legacy.csv，统一走实际导入路径。上传限制4MB，拒绝不带时区的时间。业务键按字符串保留，Decimal参与计算，空值不会当零。

`POST /api/runs` 接收sample或csv；`GET /api/runs`返回历史批次；`POST /api/runs/<id>/review`需要version与decision；`GET /api/runs/<id>/archive`返回唯一证据导出。

## 范围

样例均为虚构。单机复核工具不连接生产系统；不提供登录、远端数据库或自动支付。内存计算按4MB输入上限控制，SQLite事务保证结果完整写入。

## 目录

app为服务与规则，sample_data为输入，tests为测试，docs为定义与深度登记。
