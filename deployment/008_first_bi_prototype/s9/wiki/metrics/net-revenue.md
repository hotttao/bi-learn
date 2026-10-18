# 净收入 net_revenue@1

已审核范围：S8 独立合成销售数据。受控同义词“净销售额”，人民币元；订单实付减累计已记录退款，不是财务确认收入或毛利。

先读原生 Wren 文件：[订单](../../wren/models/sales_orders/metadata.yml)、[退款](../../wren/models/sales_refunds/metadata.yml)。字段以 YAML 为准。

`sales_orders` 每行一订单，order_id 全局唯一。退款先按 dept_id、order_id 汇总，再 LEFT JOIN 订单；无退款为零，得到一行一订单的 paid_amount 减退款。不要把原始退款明细或订单行直接关联后重复累计实付。客户身份是 dept_id＋customer_id。

日期为 Asia/Shanghai 的订单业务 DATE，退款归属原订单日，区间起含止不含。缺失维度保留订单。基础不重复总额从一行一订单净额求和，不从多标签关联后的行求和。

依据：[原始审核规则](../../raw/reviewed-sales-rules.md)。该来源只读；本文是其按主题组织的派生说明，不是新的独立指标定义。
