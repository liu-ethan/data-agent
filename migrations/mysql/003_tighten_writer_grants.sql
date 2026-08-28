-- 灌数完成后由 root 执行：writer 不得改订单/支付等业务事实表。
USE `data-agent-ecommerce`;

REVOKE INSERT, UPDATE ON `data-agent-ecommerce`.* FROM 'da_writer'@'localhost';
GRANT SELECT ON `data-agent-ecommerce`.* TO 'da_writer'@'localhost';
GRANT SELECT, INSERT, UPDATE ON `data-agent-ecommerce`.`dim_sku` TO 'da_writer'@'localhost';
GRANT SELECT, INSERT, UPDATE ON `data-agent-ecommerce`.`da_write_receipt` TO 'da_writer'@'localhost';
GRANT SELECT, INSERT, UPDATE ON `data-agent-ecommerce`.`da_write_audit` TO 'da_writer'@'localhost';
FLUSH PRIVILEGES;
