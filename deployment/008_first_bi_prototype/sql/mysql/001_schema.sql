CREATE TABLE IF NOT EXISTS orders (
    order_id BIGINT NOT NULL PRIMARY KEY COMMENT '全局唯一订单 ID',
    dept_id CHAR(1) NOT NULL COMMENT '订单归属部门，参与授权与关联',
    customer_id BIGINT NOT NULL COMMENT '仅部门内唯一的客户 ID',
    order_date DATE NOT NULL COMMENT 'Asia/Shanghai 业务日期',
    paid_amount DECIMAL(12, 2) NOT NULL COMMENT '人民币元，已实付金额',
    CONSTRAINT uq_order_department UNIQUE (dept_id, order_id),
    CONSTRAINT ck_order_department CHECK (dept_id IN ('A', 'B')),
    CONSTRAINT ck_paid_amount CHECK (paid_amount >= 0)
) ENGINE=InnoDB COMMENT='一行一订单；跨库客户引用由校验程序验证';

CREATE TABLE IF NOT EXISTS refunds (
    refund_id BIGINT NOT NULL PRIMARY KEY COMMENT '全局唯一退款 ID',
    dept_id CHAR(1) NOT NULL,
    order_id BIGINT NOT NULL,
    refund_date DATE NOT NULL COMMENT '退款发生日；本实验统计回溯订单日',
    refund_amount DECIMAL(12, 2) NOT NULL COMMENT '人民币元，已记录退款',
    CONSTRAINT fk_refund_order FOREIGN KEY (dept_id, order_id)
        REFERENCES orders (dept_id, order_id),
    CONSTRAINT ck_refund_amount CHECK (refund_amount >= 0)
) ENGINE=InnoDB COMMENT='一行一退款；关联订单前须先汇总';
