# 客户当前行业

先读 [净收入基础指标](../metrics/net-revenue.md) 及其链接的订单/退款模型，再读 [原生客户模型](../../wren/models/crm_customers/metadata.yml)。

角色：当前行业归属。用 dept_id、customer_id 联合关联 `crm_customers`，不能只用 customer_id；缺失客户保留订单并归入“未知行业”。客户更新当前行业后，历史订单会按新的当前行业重新分组。本路径不能回答下单时行业。

依据：[审核规则来源](../../raw/reviewed-sales-rules.md)。金额和字段只沿相关依赖读取，不需要历史行业、标签、订单行或财务模型。
