# 下单时客户行业

先读 [净收入基础指标](../metrics/net-revenue.md) 及其链接的订单/退款模型，再读 [原生行业历史模型](../../wren/models/crm_industry_history/metadata.yml)。

角色：订单发生时的行业归属。用 dept_id、customer_id 联合关联历史，要求 order_date>=valid_from AND order_date<valid_to。有效期起含止不含；无匹配保留为“未知行业”，不能改用当前行业替代。有重叠区间时拒绝发布，不能任选一行；可先执行只读质量核查。

依据：[审核规则来源](../../raw/reviewed-sales-rules.md)。本能力仅属于本独立实验发布包，不改变主项目当前行业语义。
