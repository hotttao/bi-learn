# 已审核的 S8 合成销售分析规则
净收入 net_revenue@1，受控别名净销售额，是订单实付减累计已记录退款，人民币元，不是 finance_revenue_entries 的财务确认收入。没有规则时不能凭表名确认指标口径。
sales_orders 每行一订单，order_id 全局唯一；退款先按 dept_id、order_id 聚合，再 LEFT JOIN 订单；不得直接 JOIN 多笔退款或 order_lines 后重复累计实付。无退款为零。
客户身份必须由 dept_id、customer_id 两列确定，不能单用 customer_id；缺失客户保留订单，行业归为“未知行业”。
当前行业来自 crm_customers；下单时行业来自 crm_industry_history，按同一客户身份并使用 order_date >= valid_from AND order_date < valid_to。有效期重叠必须拒绝发布；没有匹配时保留订单归为未知行业。
客户标签 crm_customer_tags 是允许重叠的多对多关系；先得到一行一订单净额，再关联标签。无标签保留为“无标签”。每组统计其成员订单完整净额，不分摊；不同标签组不能相加作为总净收入，另查去重前基础订单净额。
业务日期 Asia/Shanghai，退款归属原订单日，显式 DATE 边界起含止不含。这里只是合成实验，未实现部门授权。其他目录模型是上下文噪声，不是已审核的销售事实，不替换 sales_orders/销售退款关系。
