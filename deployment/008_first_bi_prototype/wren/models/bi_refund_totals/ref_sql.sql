SELECT dept_id AS refund_dept_id, order_id AS refund_order_id, SUM(refund_amount) AS refund_total
FROM mysql.sales.refunds
GROUP BY dept_id, order_id
