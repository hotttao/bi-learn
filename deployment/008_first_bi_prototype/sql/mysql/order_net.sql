-- One row per order; still independent of the CRM database.
SELECT o.order_id, o.dept_id, o.customer_id, o.order_date,
       o.paid_amount, COALESCE(r.refund_amount, 0) AS refund_amount,
       o.paid_amount - COALESCE(r.refund_amount, 0) AS net_amount
FROM orders AS o
LEFT JOIN (
    SELECT dept_id, order_id, SUM(refund_amount) AS refund_amount
    FROM refunds
    GROUP BY dept_id, order_id
) AS r ON r.dept_id = o.dept_id AND r.order_id = o.order_id
ORDER BY o.order_id;
