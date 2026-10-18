WITH r AS (SELECT dept_id,order_id,SUM(refund_amount) AS refund FROM sales_refunds GROUP BY dept_id,order_id),
n AS (SELECT o.order_id,o.dept_id,o.customer_id,o.order_date,o.paid_amount-COALESCE(r.refund,0) AS net_amount FROM sales_orders o LEFT JOIN r ON o.dept_id=r.dept_id AND o.order_id=r.order_id)
SELECT COALESCE(t.tag,'无标签') AS label,SUM(n.net_amount) AS amount FROM n LEFT JOIN crm_customer_tags t ON n.dept_id=t.dept_id AND n.customer_id=t.customer_id GROUP BY COALESCE(t.tag,'无标签')
