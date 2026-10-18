WITH r AS (SELECT dept_id,order_id,SUM(refund_amount) AS refund FROM sales_refunds GROUP BY dept_id,order_id),
n AS (SELECT o.order_id,o.dept_id,o.customer_id,o.order_date,o.paid_amount-COALESCE(r.refund,0) AS net_amount FROM sales_orders o LEFT JOIN r ON o.dept_id=r.dept_id AND o.order_id=r.order_id)
SELECT COALESCE(h.industry,'未知行业') AS label,SUM(n.net_amount) AS amount FROM n LEFT JOIN crm_industry_history h ON n.dept_id=h.dept_id AND n.customer_id=h.customer_id AND n.order_date>=h.valid_from AND n.order_date<h.valid_to GROUP BY COALESCE(h.industry,'未知行业')
