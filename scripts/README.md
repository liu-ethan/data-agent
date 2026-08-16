# MySQL 本地环境脚本

`mysql_env.sh` 用于初始化和排查 Data Runtime Agent 的本地 MySQL 环境。

## 初始化

在项目根目录执行：

```bash
chmod +x scripts/mysql_env.sh
scripts/mysql_env.sh init
```

脚本内置了本地开发默认密码：

- root 密码：当前配置为 `lxh152732`，对应现有本机 MySQL root 密码；
- `agent_migration` 密码：`123456`；
- `agent_reader` 密码：`123456`；
- `agent_writer` 密码：`123456`。

如果本机 root 密码已经改成其他值，修改 `mysql_env.sh` 顶部的
`MYSQL_ROOT_PASSWORD`；也可以通过环境变量覆盖脚本中的默认值。

脚本可以重复运行。重复运行会确认账号密码并重新应用权限，不会删除数据库或表。

初始化完成后，根目录的 `config.yaml` 已使用相同的本地默认密码：

```yaml
mysql:
  accounts:
    migration:
      username: agent_migration
      password: "123456"
    reader:
      username: agent_reader
      password: "123456"
    writer:
      username: agent_writer
      password: "123456"
```

`config.yaml` 已被 `.gitignore` 忽略，不应提交到代码仓库。

## 排查

```bash
# 检查 root、数据库、三个应用账号连接、权限和表
scripts/mysql_env.sh check

# 只查看三个应用账号的权限
scripts/mysql_env.sh grants

# 查看 data_agent 当前的表
scripts/mysql_env.sh tables
```

`check` 也可以写成 `status`。脚本默认使用上面的本地密码；也可以通过环境变量临时覆盖：

```bash
MYSQL_MIGRATION_PASSWORD='...' \
MYSQL_READER_PASSWORD='...' \
MYSQL_WRITER_PASSWORD='...' \
scripts/mysql_env.sh check
```

如果 MySQL 不在默认地址，可以覆盖连接参数：

```bash
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 scripts/mysql_env.sh check
```

## 权限边界

| 账号 | 权限 |
| --- | --- |
| `agent_migration` | `data_agent.*` 全部权限，用于迁移和建表 |
| `agent_reader` | `SELECT`, `SHOW VIEW` |
| `agent_writer` | `SELECT`, `INSERT`, `UPDATE` |

脚本不创建应用层登录用户。`config.yaml` 中的 `auth.demo_users` 是运行时 demo 用户，与 MySQL 账号不是一回事。

## 注意事项

密码现在明确写在本地脚本和被忽略的 `config.yaml` 中，仅适用于本机开发。生产环境应改用环境变量或密钥管理服务，并避免把密码写入命令历史。

如果应用运行在 Docker 容器中，需要把 `MYSQL_ACCOUNT_HOST` 改成应用容器的网络来源，或者为对应 host 单独创建账号；不要在生产环境直接使用 `'%'` 放开来源。
