BEGIN;
INSERT INTO public.customers (dept_id, customer_id, industry) VALUES
    ('A', 1, '软件'),
    ('A', 2, '零售'),
    ('B', 1, '软件'),
    ('B', 2, '零售')
ON CONFLICT (dept_id, customer_id) DO NOTHING;
COMMIT;
