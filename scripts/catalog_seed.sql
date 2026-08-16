-- Deterministic catalog seed. It is intentionally separate from business rows
-- so catalog versions can be advanced without rewriting transaction data.
INSERT INTO catalog_sources(source_id,name,domain,catalog_version,owner,created_at) VALUES
('mysql_ecommerce_local','Ecommerce MySQL','ECOMMERCE_TRADE','catalog_v1','data-platform','2026-08-16 10:00:00')
ON DUPLICATE KEY UPDATE name=VALUES(name),catalog_version=VALUES(catalog_version);
INSERT INTO catalog_objects(object_id,source_id,object_name,grain,object_type,catalog_version) VALUES
('obj_shops','mysql_ecommerce_local','shops','shop','TABLE','catalog_v1'),
('obj_users','mysql_ecommerce_local','users','buyer','TABLE','catalog_v1'),
('obj_categories','mysql_ecommerce_local','categories','category','TABLE','catalog_v1'),
('obj_products','mysql_ecommerce_local','products','product','TABLE','catalog_v1'),
('obj_orders','mysql_ecommerce_local','orders','order','TABLE','catalog_v1'),
('obj_order_items','mysql_ecommerce_local','order_items','order_item','TABLE','catalog_v1'),
('obj_refunds','mysql_ecommerce_local','refunds','refund','TABLE','catalog_v1'),
('obj_refund_items','mysql_ecommerce_local','refund_items','refund_item','TABLE','catalog_v1')
ON DUPLICATE KEY UPDATE grain=VALUES(grain),catalog_version=VALUES(catalog_version);
INSERT INTO catalog_fields(field_id,object_id,field_name,data_type,nullable,classification,is_time_field,catalog_version) VALUES
('field_shops_shop_id','obj_shops','shop_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_shops_shop_name','obj_shops','shop_name','VARCHAR',FALSE,'BUSINESS',FALSE,'catalog_v1'),
('field_shops_region_name','obj_shops','region_name','VARCHAR',FALSE,'BUSINESS',FALSE,'catalog_v1'),
('field_shops_status','obj_shops','status','VARCHAR',FALSE,'STATUS',FALSE,'catalog_v1'),
('field_users_user_id','obj_users','user_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_users_phone','obj_users','phone','VARCHAR',FALSE,'PHONE',FALSE,'catalog_v1'),
('field_users_id_number','obj_users','id_number','VARCHAR',FALSE,'ID_CARD',FALSE,'catalog_v1'),
('field_users_created_at','obj_users','created_at','DATETIME',FALSE,'BUSINESS_TIME',TRUE,'catalog_v1'),
('field_categories_category_id','obj_categories','category_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_categories_category_name','obj_categories','category_name','VARCHAR',FALSE,'BUSINESS',FALSE,'catalog_v1'),
('field_products_product_id','obj_products','product_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_products_shop_id','obj_products','shop_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_products_category_id','obj_products','category_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_products_product_name','obj_products','product_name','VARCHAR',FALSE,'BUSINESS',FALSE,'catalog_v1'),
('field_orders_order_id','obj_orders','order_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_orders_user_id','obj_orders','user_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_orders_shop_id','obj_orders','shop_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_orders_status','obj_orders','status','VARCHAR',FALSE,'STATUS',FALSE,'catalog_v1'),
('field_orders_paid_at','obj_orders','paid_at','DATETIME',TRUE,'BUSINESS_TIME',TRUE,'catalog_v1'),
('field_orders_pay_amount','obj_orders','pay_amount','DECIMAL',FALSE,'AMOUNT',FALSE,'catalog_v1'),
('field_order_items_item_id','obj_order_items','item_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_order_items_order_id','obj_order_items','order_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_order_items_shop_id','obj_order_items','shop_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_order_items_product_id','obj_order_items','product_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_order_items_quantity','obj_order_items','quantity','INT',FALSE,'MEASURE',FALSE,'catalog_v1'),
('field_order_items_item_paid_amount','obj_order_items','item_paid_amount','DECIMAL',FALSE,'AMOUNT',FALSE,'catalog_v1'),
('field_refunds_refund_id','obj_refunds','refund_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_refunds_order_id','obj_refunds','order_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_refunds_shop_id','obj_refunds','shop_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_refunds_status','obj_refunds','status','VARCHAR',FALSE,'STATUS',FALSE,'catalog_v1'),
('field_refunds_refund_amount','obj_refunds','refund_amount','DECIMAL',FALSE,'AMOUNT',FALSE,'catalog_v1'),
('field_refunds_refunded_at','obj_refunds','refunded_at','DATETIME',TRUE,'BUSINESS_TIME',TRUE,'catalog_v1'),
('field_refund_items_refund_item_id','obj_refund_items','refund_item_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_refund_items_refund_id','obj_refund_items','refund_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_refund_items_shop_id','obj_refund_items','shop_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_refund_items_order_item_id','obj_refund_items','order_item_id','VARCHAR',FALSE,'IDENTIFIER',FALSE,'catalog_v1'),
('field_refund_items_refund_amount','obj_refund_items','refund_amount','DECIMAL',FALSE,'AMOUNT',FALSE,'catalog_v1')
ON DUPLICATE KEY UPDATE data_type=VALUES(data_type),classification=VALUES(classification),catalog_version=VALUES(catalog_version);
INSERT INTO metric_definitions(metric_id,name,formula,time_field,grain_json,required_filters_json,forbidden_join_patterns_json,null_policy,rounding,metric_version) VALUES
('gmv','支付 GMV','SUM(order_items.item_paid_amount)','orders.paid_at','["day","category","shop"]','["orders.status = PAID"]','["raw_fact_to_raw_fact_sum"]','empty_denominator_returns_null','DECIMAL(2)','metric_v1'),
('paid_order_count','支付订单数','COUNT(DISTINCT orders.order_id)','orders.paid_at','["day","shop"]','["orders.status = PAID"]','["count_star_after_one_to_many"]','empty_denominator_returns_null','INTEGER','metric_v1'),
('paid_buyer_count','支付买家数','COUNT(DISTINCT orders.user_id)','orders.paid_at','["day","shop"]','["orders.status = PAID"]','[]','empty_denominator_returns_null','INTEGER','metric_v1'),
('refund_amount','退款金额','SUM(refunds.refund_amount)','refunds.refunded_at','["day","shop"]','["refunds.status = SUCCESS"]','["raw_fact_to_raw_fact_sum"]','empty_denominator_returns_null','DECIMAL(2)','metric_v1'),
('refund_rate','金额退款率','refund_amount / gmv','refunds.refunded_at','["day","shop"]','["refunds.status = SUCCESS"]','["zero_denominator"]','empty_denominator_returns_null','DECIMAL(4)','metric_v1')
ON DUPLICATE KEY UPDATE name=VALUES(name),formula=VALUES(formula),metric_version=VALUES(metric_version);
INSERT INTO table_relations(relation_id,left_ref,right_ref,cardinality,verified,relation_version) VALUES
('orders_to_order_items','orders.order_id','order_items.order_id','one_to_many',TRUE,'relation_v1'),
('order_items_to_products','order_items.product_id','products.product_id','many_to_one',TRUE,'relation_v1'),
('products_to_categories','products.category_id','categories.category_id','many_to_one',TRUE,'relation_v1'),
('orders_to_shops','orders.shop_id','shops.shop_id','many_to_one',TRUE,'relation_v1')
ON DUPLICATE KEY UPDATE verified=VALUES(verified),relation_version=VALUES(relation_version);
INSERT INTO entity_aliases(alias_id,alias_text,target_type,target_id,version) VALUES
('alias_region_east','华东','REGION','region_east','alias_v1'),
('alias_region_east_alt','华东地区','REGION','region_east','alias_v1'),
('alias_gmv','销售额','METRIC','gmv','alias_v1'),
('alias_gmv_cny','成交额','METRIC','gmv','alias_v1'),
('alias_category','类目','DIMENSION','categories.category_name','alias_v1'),
('alias_category_cn','品类','DIMENSION','categories.category_name','alias_v1')
ON DUPLICATE KEY UPDATE target_id=VALUES(target_id),version=VALUES(version);
