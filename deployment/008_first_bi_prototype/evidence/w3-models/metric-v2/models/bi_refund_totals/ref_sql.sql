SELECT dept_id AS refund_dept_id,order_id AS refund_order_id,SUM(refund_amount) AS refund_total FROM (SELECT * FROM (VALUES ('A',1,CAST('10.00' AS DECIMAL(12,2))),
('A',1,CAST('20.00' AS DECIMAL(12,2))),
('A',2,CAST('5.00' AS DECIMAL(12,2))),
('B',4,CAST('10.00' AS DECIMAL(12,2)))) AS fixture(dept_id,order_id,refund_amount)) r GROUP BY dept_id,order_id
