SELECT * FROM (SELECT * FROM (VALUES (1,'A',1,DATE '2026-10-01',CAST('100.00' AS DECIMAL(12,2)),'paid'),
(2,'A',2,DATE '2026-10-02',CAST('80.00' AS DECIMAL(12,2)),'paid'),
(3,'B',1,DATE '2026-10-01',CAST('50.00' AS DECIMAL(12,2)),'settled'),
(4,'B',2,DATE '2026-10-02',CAST('60.00' AS DECIMAL(12,2)),'paid')) AS fixture(order_id,dept_id,customer_id,order_date,paid_amount,status)) s WHERE status IN ('paid','settled')
