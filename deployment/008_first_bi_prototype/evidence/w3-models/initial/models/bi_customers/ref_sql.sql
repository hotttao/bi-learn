SELECT dept_id AS customer_dept_id,customer_id AS customer_local_id,industry FROM (SELECT * FROM (VALUES ('A',1,'软件'),
('A',2,'零售'),
('B',3,'软件'),
('B',4,'零售')) AS fixture(dept_id,customer_id,industry)) c
