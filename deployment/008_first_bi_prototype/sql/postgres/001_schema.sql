CREATE TABLE IF NOT EXISTS public.customers (
    dept_id CHAR(1) NOT NULL CHECK (dept_id IN ('A', 'B')),
    customer_id BIGINT NOT NULL,
    industry TEXT NOT NULL CHECK (length(trim(industry)) > 0),
    PRIMARY KEY (dept_id, customer_id)
);
COMMENT ON TABLE public.customers IS '一行一部门内客户；当前行业，不是历史快照';
COMMENT ON COLUMN public.customers.customer_id IS '必须与 dept_id 组合使用';
COMMENT ON COLUMN public.customers.industry IS '当前行业；更新后历史订单按当前行业重分组';
