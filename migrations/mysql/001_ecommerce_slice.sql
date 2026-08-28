-- 电商开发切片：只放业务事实 + 写入回执/审计。
-- Agent 目录、用户、向量、Checkpoint、结果元数据一律在 SQLite，禁止写入本库。
-- 库名含连字符，必须使用反引号：`data-agent-ecommerce`
--
-- 账号边界（本文件末尾会收紧 writer）：
--   da_reader = SELECT
--   da_writer = 全表 SELECT + 仅 dim_sku / da_write_receipt / da_write_audit 的 INSERT, UPDATE

USE `data-agent-ecommerce`;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS da_write_audit;
DROP TABLE IF EXISTS da_write_receipt;
DROP TABLE IF EXISTS fact_ad_spend;
DROP TABLE IF EXISTS fact_traffic;
DROP TABLE IF EXISTS fact_refund;
DROP TABLE IF EXISTS fact_payment;
DROP TABLE IF EXISTS fact_order_item;
DROP TABLE IF EXISTS fact_order;
DROP TABLE IF EXISTS dim_campaign;
DROP TABLE IF EXISTS dim_sku;
DROP TABLE IF EXISTS dim_category;
DROP TABLE IF EXISTS dim_channel;
DROP TABLE IF EXISTS dim_user;
DROP TABLE IF EXISTS dim_store;

SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE dim_store (
  id            BIGINT       NOT NULL,
  store_code    VARCHAR(32)  NOT NULL,
  store_name    VARCHAR(64)  NOT NULL COMMENT '门店名称',
  city          VARCHAR(32)  NOT NULL COMMENT '城市',
  status        VARCHAR(16)  NOT NULL COMMENT '营业状态：open / closed',
  opened_at     DATETIME     NOT NULL COMMENT '开业时间',
  created_at    DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_store_code (store_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='门店维度；一行一个线下店或电商仓店';

CREATE TABLE dim_user (
  id              BIGINT       NOT NULL,
  user_code       VARCHAR(32)  NOT NULL,
  nick_name       VARCHAR(64)  NOT NULL COMMENT '用户昵称',
  status          VARCHAR(16)  NOT NULL COMMENT '账号状态：active / inactive',
  first_order_at  DATETIME     NULL COMMENT '历史首单时间，用于新客口径',
  created_at      DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_code (user_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='用户维度；一行一个买家';

CREATE TABLE dim_category (
  id          BIGINT       NOT NULL,
  cat_code    VARCHAR(32)  NOT NULL,
  cat_name    VARCHAR(64)  NOT NULL COMMENT '品类名称',
  parent_id   BIGINT       NULL COMMENT '父品类，顶级为 NULL',
  status      VARCHAR(16)  NOT NULL COMMENT '启用状态：active / inactive',
  created_at  DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_cat_code (cat_code),
  KEY idx_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品品类；品类 GMV 必须经 SKU 关联，禁止把订单金额直接 JOIN 到品类';

CREATE TABLE dim_channel (
  id            BIGINT       NOT NULL,
  channel_code  VARCHAR(32)  NOT NULL,
  channel_name  VARCHAR(64)  NOT NULL COMMENT '渠道名称',
  channel_type  VARCHAR(16)  NOT NULL COMMENT '渠道类型：app / mini_program / marketplace / content',
  status        VARCHAR(16)  NOT NULL,
  created_at    DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_channel_code (channel_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='流量与成交渠道';

CREATE TABLE dim_sku (
  id              BIGINT        NOT NULL,
  sku_code        VARCHAR(32)   NOT NULL,
  sku_name        VARCHAR(128)  NOT NULL COMMENT 'SKU 商品名',
  category_id     BIGINT        NOT NULL COMMENT '所属品类',
  list_price      DECIMAL(12,2) NOT NULL COMMENT '挂牌价',
  status          VARCHAR(16)   NOT NULL COMMENT '上下架：on_sale / off_sale',
  inventory_qty   INT           NOT NULL COMMENT '当前可售库存',
  row_version     INT           NOT NULL DEFAULT 1 COMMENT '乐观锁版本，写入时必须带入',
  created_at      DATETIME      NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sku_code (sku_code),
  KEY idx_sku_category (category_id),
  KEY idx_sku_status (status),
  CONSTRAINT fk_sku_category FOREIGN KEY (category_id) REFERENCES dim_category (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='SKU 商品；受控写入白名单目标（上下架、调库存）';

CREATE TABLE dim_campaign (
  id              BIGINT       NOT NULL,
  campaign_code   VARCHAR(32)  NOT NULL,
  campaign_name   VARCHAR(64)  NOT NULL COMMENT '活动名称',
  channel_id      BIGINT       NOT NULL,
  status          VARCHAR(16)  NOT NULL COMMENT '活动状态：active / ended',
  start_at        DATETIME     NOT NULL,
  end_at          DATETIME     NOT NULL,
  created_at      DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_campaign_code (campaign_code),
  KEY idx_campaign_channel (channel_id),
  CONSTRAINT fk_campaign_channel FOREIGN KEY (channel_id) REFERENCES dim_channel (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='营销活动；广告 ROI 归因用';

CREATE TABLE fact_order (
  id            BIGINT        NOT NULL,
  order_no      VARCHAR(32)   NOT NULL,
  user_id       BIGINT        NOT NULL,
  store_id      BIGINT        NOT NULL,
  channel_id    BIGINT        NOT NULL,
  campaign_id   BIGINT        NULL COMMENT '归因活动，可空',
  status        VARCHAR(16)   NOT NULL COMMENT 'unpaid / paid / shipped / completed / cancelled',
  amount        DECIMAL(12,2) NOT NULL COMMENT '订单商品原价合计',
  pay_amt       DECIMAL(12,2) NOT NULL COMMENT '实付金额（扣除优惠后）',
  created_at    DATETIME      NOT NULL COMMENT '下单时间',
  paid_at       DATETIME      NULL COMMENT '支付成功时间',
  completed_at  DATETIME      NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_order_no (order_no),
  KEY idx_order_user (user_id),
  KEY idx_order_store (store_id),
  KEY idx_order_channel (channel_id),
  KEY idx_order_campaign (campaign_id),
  KEY idx_order_created (created_at),
  KEY idx_order_paid (paid_at),
  KEY idx_order_status (status),
  CONSTRAINT fk_order_user FOREIGN KEY (user_id) REFERENCES dim_user (id),
  CONSTRAINT fk_order_store FOREIGN KEY (store_id) REFERENCES dim_store (id),
  CONSTRAINT fk_order_channel FOREIGN KEY (channel_id) REFERENCES dim_channel (id),
  CONSTRAINT fk_order_campaign FOREIGN KEY (campaign_id) REFERENCES dim_campaign (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='订单事实；一行一笔订单。GMV 在订单行粒度汇总，禁止用本表 amount 直接 JOIN 品类';

CREATE TABLE fact_order_item (
  id         BIGINT        NOT NULL,
  order_id   BIGINT        NOT NULL,
  sku_id     BIGINT        NOT NULL,
  qty        INT           NOT NULL COMMENT '购买件数',
  price      DECIMAL(12,2) NOT NULL COMMENT '成交时挂牌单价',
  amount     DECIMAL(12,2) NOT NULL COMMENT '行原价小计 = price * qty',
  pay_amt    DECIMAL(12,2) NOT NULL COMMENT '行实付金额',
  status     VARCHAR(16)   NOT NULL COMMENT '行状态：normal / refunded / cancelled',
  created_at DATETIME      NOT NULL,
  PRIMARY KEY (id),
  KEY idx_item_order (order_id),
  KEY idx_item_sku (sku_id),
  CONSTRAINT fk_item_order FOREIGN KEY (order_id) REFERENCES fact_order (id),
  CONSTRAINT fk_item_sku FOREIGN KEY (sku_id) REFERENCES dim_sku (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='订单行；GMV / 实付 GMV 的 grain 表';

CREATE TABLE fact_payment (
  id         BIGINT        NOT NULL,
  order_id   BIGINT        NOT NULL,
  amount     DECIMAL(12,2) NOT NULL COMMENT '支付金额',
  status     VARCHAR(16)   NOT NULL COMMENT 'success / failed',
  paid_at    DATETIME      NULL COMMENT '支付完成时间',
  created_at DATETIME      NOT NULL,
  PRIMARY KEY (id),
  KEY idx_pay_order (order_id),
  KEY idx_pay_paid_at (paid_at),
  CONSTRAINT fk_pay_order FOREIGN KEY (order_id) REFERENCES fact_order (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='支付事实；一行一次支付尝试';

CREATE TABLE fact_refund (
  id            BIGINT        NOT NULL,
  order_id      BIGINT        NOT NULL,
  order_item_id BIGINT        NOT NULL,
  amount        DECIMAL(12,2) NOT NULL COMMENT '退款金额',
  status        VARCHAR(16)   NOT NULL COMMENT 'success / pending / rejected',
  refunded_at   DATETIME      NULL COMMENT '退款成功时间',
  created_at    DATETIME      NOT NULL,
  PRIMARY KEY (id),
  KEY idx_refund_order (order_id),
  KEY idx_refund_item (order_item_id),
  KEY idx_refunded_at (refunded_at),
  CONSTRAINT fk_refund_order FOREIGN KEY (order_id) REFERENCES fact_order (id),
  CONSTRAINT fk_refund_item FOREIGN KEY (order_item_id) REFERENCES fact_order_item (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='退款事实；允许一笔订单多次退款，金额退款率用本表 amount';

CREATE TABLE fact_traffic (
  id          BIGINT       NOT NULL,
  dt          DATE         NOT NULL COMMENT '统计日',
  store_id    BIGINT       NOT NULL,
  channel_id  BIGINT       NOT NULL,
  visitor_cnt INT          NOT NULL COMMENT '独立访客数',
  status      VARCHAR(16)  NOT NULL,
  created_at  DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_traffic_day (dt, store_id, channel_id),
  KEY idx_traffic_store (store_id),
  CONSTRAINT fk_traffic_store FOREIGN KEY (store_id) REFERENCES dim_store (id),
  CONSTRAINT fk_traffic_channel FOREIGN KEY (channel_id) REFERENCES dim_channel (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='流量日事实；转化率分母。一行=某店某渠道某日访客';

CREATE TABLE fact_ad_spend (
  id           BIGINT        NOT NULL,
  dt_month     DATE          NOT NULL COMMENT '月份第一天',
  campaign_id  BIGINT        NOT NULL,
  channel_id   BIGINT        NOT NULL,
  amount       DECIMAL(12,2) NOT NULL COMMENT '广告花费',
  status       VARCHAR(16)   NOT NULL,
  created_at   DATETIME      NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ad_month (dt_month, campaign_id, channel_id),
  CONSTRAINT fk_ad_campaign FOREIGN KEY (campaign_id) REFERENCES dim_campaign (id),
  CONSTRAINT fk_ad_channel FOREIGN KEY (channel_id) REFERENCES dim_channel (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='广告花费月事实；广告 ROI 分母';

CREATE TABLE da_write_receipt (
  operation_id   VARCHAR(36)  NOT NULL,
  request_hash   CHAR(64)     NOT NULL,
  operation_type VARCHAR(64)  NOT NULL,
  status         ENUM('pending','committed','unknown') NOT NULL,
  affected_rows  INT          NULL,
  audit_id       VARCHAR(36)  NULL,
  payload_json   JSON         NOT NULL,
  created_at     TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (operation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='写入回执；必须与业务变更同实例同事务，不能放到 SQLite';

CREATE TABLE da_write_audit (
  audit_id       VARCHAR(36)  NOT NULL,
  operation_id   VARCHAR(36)  NOT NULL,
  actor_user_id  VARCHAR(64)  NOT NULL,
  operation_type VARCHAR(64)  NOT NULL,
  target_table   VARCHAR(128) NOT NULL,
  target_pk      JSON         NOT NULL,
  before_json    JSON         NULL,
  after_json     JSON         NULL,
  created_at     TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (audit_id),
  KEY idx_operation (operation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='写入审计；与回执、业务表同一 InnoDB 事务提交';
