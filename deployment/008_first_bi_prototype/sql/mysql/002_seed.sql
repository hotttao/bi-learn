-- Insert missing baseline IDs only. Never overwrite later manual experiments.
START TRANSACTION;
INSERT INTO orders
SELECT 1, 'A', 1, '2026-10-01', 100.00 WHERE NOT EXISTS (SELECT 1 FROM orders WHERE order_id = 1);
INSERT INTO orders
SELECT 2, 'A', 2, '2026-10-02', 80.00 WHERE NOT EXISTS (SELECT 1 FROM orders WHERE order_id = 2);
INSERT INTO orders
SELECT 3, 'B', 1, '2026-10-01', 50.00 WHERE NOT EXISTS (SELECT 1 FROM orders WHERE order_id = 3);
INSERT INTO orders
SELECT 4, 'B', 2, '2026-10-02', 60.00 WHERE NOT EXISTS (SELECT 1 FROM orders WHERE order_id = 4);

INSERT INTO refunds
SELECT 1, 'A', 1, '2026-10-02', 10.00 WHERE NOT EXISTS (SELECT 1 FROM refunds WHERE refund_id = 1);
INSERT INTO refunds
SELECT 2, 'A', 1, '2026-10-03', 20.00 WHERE NOT EXISTS (SELECT 1 FROM refunds WHERE refund_id = 2);
INSERT INTO refunds
SELECT 3, 'A', 2, '2026-10-03', 5.00 WHERE NOT EXISTS (SELECT 1 FROM refunds WHERE refund_id = 3);
INSERT INTO refunds
SELECT 4, 'B', 4, '2026-10-04', 10.00 WHERE NOT EXISTS (SELECT 1 FROM refunds WHERE refund_id = 4);
COMMIT;
