-- Local development identities only.  Production users/scopes are provisioned
-- by the identity administration workflow, never from application requests.
INSERT INTO app_users(user_id,role_name,active,policy_version,created_at,updated_at) VALUES
('u_demo_user','USER',TRUE,'policy_local_v2',UTC_TIMESTAMP(),UTC_TIMESTAMP()),
('u_demo_admin','ADMIN',TRUE,'policy_local_v2',UTC_TIMESTAMP(),UTC_TIMESTAMP())
ON DUPLICATE KEY UPDATE role_name=VALUES(role_name),active=VALUES(active),policy_version=VALUES(policy_version),updated_at=UTC_TIMESTAMP();
INSERT INTO app_user_shop_scopes(user_id,shop_id,policy_version) VALUES
('u_demo_user','shop_001','policy_local_v2'),('u_demo_user','shop_002','policy_local_v2'),
('u_demo_admin','shop_001','policy_local_v2'),('u_demo_admin','shop_002','policy_local_v2'),('u_demo_admin','shop_003','policy_local_v2')
ON DUPLICATE KEY UPDATE policy_version=VALUES(policy_version);
