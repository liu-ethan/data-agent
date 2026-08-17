-- Data Runtime Agent Spec 01 business schema.
-- Lives in the dedicated business database (data_agent_ecommerce).
-- This file creates tables only; seed rows are written by mock_mysql_data.sh.
--
-- The business database is owned by the e-commerce test workload; it has no
-- knowledge of the application control plane (identities, checkpoints,
-- catalog, sessions, memories) which lives in data_agent_system.

USE data_agent_ecommerce;

CREATE TABLE IF NOT EXISTS shops (
  shop_id VARCHAR(64) NOT NULL,
  shop_name VARCHAR(128) NOT NULL,
  region_code VARCHAR(32) NOT NULL,
  region_name VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  PRIMARY KEY (shop_id),
  CONSTRAINT chk_shops_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
  user_id VARCHAR(64) NOT NULL,
  phone VARCHAR(32) NOT NULL,
  id_number VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (user_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS categories (
  category_id VARCHAR(64) NOT NULL,
  parent_id VARCHAR(64) NULL,
  category_name VARCHAR(128) NOT NULL,
  PRIMARY KEY (category_id),
  CONSTRAINT fk_categories_parent
    FOREIGN KEY (parent_id) REFERENCES categories (category_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS products (
  product_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  category_id VARCHAR(64) NOT NULL,
  product_name VARCHAR(255) NOT NULL,
  status VARCHAR(16) NOT NULL,
  PRIMARY KEY (product_id),
  KEY idx_products_shop_id (shop_id),
  KEY idx_products_category_id (category_id),
  CONSTRAINT fk_products_shop
    FOREIGN KEY (shop_id) REFERENCES shops (shop_id),
  CONSTRAINT fk_products_category
    FOREIGN KEY (category_id) REFERENCES categories (category_id),
  CONSTRAINT chk_products_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS orders (
  order_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  paid_at DATETIME NULL,
  pay_amount DECIMAL(18, 2) NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (order_id),
  KEY idx_orders_user_id (user_id),
  KEY idx_orders_shop_paid (shop_id, paid_at),
  KEY idx_orders_status_paid (status, paid_at),
  CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users (user_id),
  CONSTRAINT fk_orders_shop
    FOREIGN KEY (shop_id) REFERENCES shops (shop_id),
  CONSTRAINT chk_orders_status
    CHECK (status IN ('PAID', 'CANCELLED', 'UNPAID', 'PAYMENT_FAILED'))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS order_items (
  item_id VARCHAR(64) NOT NULL,
  order_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  product_id VARCHAR(64) NOT NULL,
  quantity INT UNSIGNED NOT NULL,
  item_paid_amount DECIMAL(18, 2) NOT NULL,
  PRIMARY KEY (item_id),
  KEY idx_order_items_order_id (order_id),
  KEY idx_order_items_shop_id (shop_id),
  KEY idx_order_items_product_id (product_id),
  CONSTRAINT fk_order_items_order
    FOREIGN KEY (order_id) REFERENCES orders (order_id),
  CONSTRAINT fk_order_items_shop
    FOREIGN KEY (shop_id) REFERENCES shops (shop_id),
  CONSTRAINT fk_order_items_product
    FOREIGN KEY (product_id) REFERENCES products (product_id),
  CONSTRAINT chk_order_items_quantity CHECK (quantity > 0)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS refunds (
  refund_id VARCHAR(64) NOT NULL,
  order_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  refund_amount DECIMAL(18, 2) NOT NULL,
  refunded_at DATETIME NULL,
  PRIMARY KEY (refund_id),
  KEY idx_refunds_order_id (order_id),
  KEY idx_refunds_shop_refunded (shop_id, refunded_at),
  KEY idx_refunds_status_refunded (status, refunded_at),
  CONSTRAINT fk_refunds_order
    FOREIGN KEY (order_id) REFERENCES orders (order_id),
  CONSTRAINT fk_refunds_shop
    FOREIGN KEY (shop_id) REFERENCES shops (shop_id),
  CONSTRAINT chk_refunds_status CHECK (status IN ('SUCCESS', 'PENDING', 'FAILED'))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS refund_items (
  refund_item_id VARCHAR(64) NOT NULL,
  refund_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  order_item_id VARCHAR(64) NOT NULL,
  refund_amount DECIMAL(18, 2) NOT NULL,
  PRIMARY KEY (refund_item_id),
  KEY idx_refund_items_refund_id (refund_id),
  KEY idx_refund_items_shop_id (shop_id),
  KEY idx_refund_items_order_item_id (order_item_id),
  CONSTRAINT fk_refund_items_refund
    FOREIGN KEY (refund_id) REFERENCES refunds (refund_id),
  CONSTRAINT fk_refund_items_shop
    FOREIGN KEY (shop_id) REFERENCES shops (shop_id),
  CONSTRAINT fk_refund_items_order_item
    FOREIGN KEY (order_item_id) REFERENCES order_items (item_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;