-- Grain: one row per order. Industry is CURRENT, not as-of-order.
WITH refund_totals AS (
    SELECT dept_id, order_id, SUM(refund_amount) AS refund_total
    FROM mysql.sales.refunds
    GROUP BY dept_id, order_id
)
SELECT o.order_id, o.dept_id, o.customer_id, o.order_date,
       COALESCE(c.industry, '未知行业') AS industry,
       o.paid_amount,
       COALESCE(r.refund_total, DECIMAL '0.00') AS refund_total,
       o.paid_amount - COALESCE(r.refund_total, DECIMAL '0.00') AS net_amount
FROM mysql.sales.orders o
LEFT JOIN refund_totals r ON o.dept_id = r.dept_id AND o.order_id = r.order_id
LEFT JOIN crm.public.customers c ON o.dept_id = c.dept_id AND o.customer_id = c.customer_id
