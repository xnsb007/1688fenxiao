import os
import pymysql
from pymysql import cursors
from app.config import MYSQL_CONFIG, DB_POOL_SIZE, DB_POOL_RECYCLE, MYSQL_TIME_ZONE
import threading
import time


class DictCursor(pymysql.cursors.DictCursor):
    pass


class PooledConnection:
    """包装连接，让 close() 实际上是归还到池中"""
    
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._closed = False
    
    def __getattr__(self, name):
        return getattr(self._conn, name)
    
    def close(self):
        """归还连接到池中"""
        if not self._closed:
            self._closed = True
            self._pool._return_connection(self._conn)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class ConnectionPool:
    """简单的数据库连接池"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._pool = []
                    cls._instance._pool_lock = threading.Lock()
                    cls._instance._created_count = 0
        return cls._instance

    @staticmethod
    def _configure_connection(conn):
        cursor = conn.cursor()
        try:
            cursor.execute('SET time_zone = %s', (MYSQL_TIME_ZONE,))
        finally:
            cursor.close()
    
    def get_connection(self, max_retries=50):
        """获取连接"""
        retries = 0
        while retries < max_retries:
            with self._pool_lock:
                # 尝试从池中获取可用连接
                while self._pool:
                    conn, last_used = self._pool.pop()
                    # 检查连接是否有效
                    try:
                        conn.ping(reconnect=True)
                        self._configure_connection(conn)
                        return PooledConnection(conn, self)
                    except:
                        # 连接无效，继续尝试下一个
                        self._created_count -= 1
                        try:
                            conn.close()
                        except:
                            pass
                        continue
                
                # 池中没有可用连接，创建新连接
                if self._created_count < DB_POOL_SIZE:
                    conn = pymysql.connect(**MYSQL_CONFIG, cursorclass=DictCursor)
                    self._configure_connection(conn)
                    self._created_count += 1
                    return PooledConnection(conn, self)
            
            # 如果达到最大连接数，等待并重试
            time.sleep(0.1)
            retries += 1
        
        # 达到最大重试次数，抛出异常
        raise Exception("Database connection pool exhausted after maximum retries")
    
    def _return_connection(self, conn):
        """归还连接（内部方法）"""
        with self._pool_lock:
            if len(self._pool) < DB_POOL_SIZE:
                self._pool.append((conn, time.time()))
            else:
                # 池已满，关闭连接
                try:
                    conn.close()
                except:
                    pass
                self._created_count -= 1


# 全局连接池实例
_db_pool = None
_pool_lock = threading.Lock()


def get_pool():
    """获取连接池实例"""
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                _db_pool = ConnectionPool()
    return _db_pool


def get_db():
    """获取数据库连接（从连接池）"""
    return get_pool().get_connection()


def release_db(conn):
    """释放数据库连接（归还到连接池）"""
    if conn:
        conn.close()


def init_db():
    print(f"[Init DB] Connecting to MySQL at {MYSQL_CONFIG.get('host')}:{MYSQL_CONFIG.get('port')}...")
    
    # 初始化数据库时使用直接连接，增加超时时间
    init_config = MYSQL_CONFIG.copy()
    init_config['connect_timeout'] = 30
    init_config['read_timeout'] = 300
    init_config['write_timeout'] = 300
    init_config['autocommit'] = True  # 启用自动提交
    
    # 重试机制
    max_retries = 3
    retry_delay = 2
    conn = None
    
    for attempt in range(max_retries):
        try:
            print(f"[Init DB] Connection attempt {attempt + 1}/{max_retries}...")
            conn = pymysql.connect(**init_config, cursorclass=DictCursor)
            ConnectionPool._configure_connection(conn)
            cursor = conn.cursor()
            print("[Init DB] Connected successfully!")
            break
        except pymysql.err.OperationalError as e:
            print(f"[Init DB] Connection failed: {e}")
            if attempt < max_retries - 1:
                print(f"[Init DB] Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                print("[Init DB] All connection attempts failed!")
                raise
    
    print("[Init DB] Testing simple query...")
    cursor.execute("SELECT 1")
    result = cursor.fetchone()
    print(f"[Init DB] Simple query result: {result}")
    
    print("[Init DB] Creating tables...")

    # 删除不需要的旧表
    old_tables = ['selection_task', 'product_snapshot', 'product_library', 'price_history', 'product_yx', 'product_df']
    for table in old_tables:
        try:
            cursor.execute(f'DROP TABLE IF EXISTS {table}')
            print(f"[Init DB] Dropped {table} (if existed)")
        except:
            pass

    print("[Init DB] Creating import_product table...")
    # 统一导入商品表 - 1688分销商品
    # 先创建基本表结构
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS import_product (
                id INT AUTO_INCREMENT PRIMARY KEY,
                offer_id VARCHAR(64),
                title VARCHAR(500),
                price DECIMAL(10,2),
                UNIQUE KEY uk_offer_id (offer_id),
                INDEX idx_offer_id (offer_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        print("[Init DB] Table import_product base created")
    except Exception as e:
        print(f"[Init DB] Warning creating import_product base: {e}")
    
    # 逐个添加列
    import_product_columns = [
        ('cost_price', 'DECIMAL(10,2)'),
        ('sell_price', 'DECIMAL(10,2)'),
        ('freight', 'DECIMAL(10,2) DEFAULT 0'),
        ('image_url', 'VARCHAR(500)'),
        ('supplier_name', 'VARCHAR(255)'),
        ('sales_count', 'INT DEFAULT 0'),
        ('deliver_days', 'INT DEFAULT 48'),
        ('stock', 'INT DEFAULT 0'),
        ('sync_status', "VARCHAR(32) DEFAULT 'pending'"),
        ('sync_at', 'TIMESTAMP NULL'),
        ('sync_error', 'TEXT'),
        ('category_id', 'VARCHAR(64)'),
        ('category_name', 'VARCHAR(255)'),
        ('erp_category_id', 'VARCHAR(64)'),
        ('erp_category_name', 'VARCHAR(255)'),
        ('offer_url', 'TEXT'),
        ('comment_count', 'INT DEFAULT 0'),
        ('month_order_count', 'INT DEFAULT 0'),
        ('month_distribution_count', 'INT DEFAULT 0'),
        ('tags', 'TEXT'),
        ('listed_time', 'VARCHAR(64)'),
        ('shop_name', 'VARCHAR(255)'),
        ('source_type', "VARCHAR(32) DEFAULT 'ALIBABA_1688'"),
        ('create_time', 'TIMESTAMP NULL'),
        ('description', 'LONGTEXT'),
        ('main_video', 'TEXT'),
        ('seven_days_refunds', 'TINYINT DEFAULT 0'),
        ('product_type', 'VARCHAR(32)'),
        ('quality_level', 'INT'),
        ('reference_price', 'VARCHAR(64)'),
        ('seller_login_id', 'VARCHAR(255)'),
        ('product_sale_info', 'LONGTEXT'),
        ('product_extend_infos', 'LONGTEXT'),
        ('sale_limit_address', 'LONGTEXT'),
        ('service_capabilities', 'LONGTEXT'),
        ('official_logistics_sku_info', 'LONGTEXT'),
        ('product_shipping_info', 'LONGTEXT'),
        ('attributes', 'TEXT'),
        ('images', 'TEXT'),
        ('sku_info', 'LONGTEXT'),
        ('sku_count', 'INT DEFAULT 0'),
        ('supplier_id', 'VARCHAR(64)'),
        ('adjusted_price', "DECIMAL(10,2) COMMENT '调价后价格（元）'"),
        ('insurance_fee', 'DECIMAL(10,2) DEFAULT 0'),
        ('status', "VARCHAR(32) DEFAULT 'active'"),
        ('listed_at', 'TIMESTAMP NULL'),
        ('last_1688_msg_time', 'VARCHAR(64)'),
        ('follow_status', "VARCHAR(32) DEFAULT 'not_followed'"),
        ('follow_at', 'TIMESTAMP NULL'),
        ('follow_error', 'TEXT'),
        ('follow_retry_count', 'INT DEFAULT 0'),
        ('last_follow_attempt_at', 'TIMESTAMP NULL'),
    ]
    
    print("[Init DB] Adding columns to import_product...")
    # 逐个添加列
    for i, (col_name, col_type) in enumerate(import_product_columns):
        try:
            cursor.execute(f'ALTER TABLE import_product ADD COLUMN {col_name} {col_type}')
            if i % 10 == 0:
                print(f"[Init DB] Added {i}/{len(import_product_columns)} columns...")
        except:
            pass
    print("[Init DB] All columns added")
    
    # 添加索引
    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_sync_status (sync_status)')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_source_type (source_type)')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_source_type_sync_status (source_type, sync_status)')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_source_type_sync_status_id (source_type, sync_status, id)')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_import_product_follow_scan (source_type, sync_status, follow_status, sync_at, id)')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_import_product_follow_retry (follow_status, last_follow_attempt_at)')
    except:
        pass
    
    print("[Init DB] Table import_product columns added")
    
    # 添加唯一索引（如果不存在）
    try:
        cursor.execute('ALTER TABLE import_product ADD UNIQUE KEY uk_offer_id (offer_id)')
    except:
        pass

    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_import_product_status (status)')
    except:
        pass

    try:
        cursor.execute('ALTER TABLE import_product ADD INDEX idx_import_product_source_status (source_type, status)')
    except:
        pass

    try:
        cursor.execute('ALTER TABLE import_product ADD COLUMN insurance_fee DECIMAL(10,2) DEFAULT 0')
    except:
        pass

    try:
        cursor.execute("UPDATE import_product SET shop_name = supplier_name WHERE (shop_name IS NULL OR shop_name = '') AND supplier_name IS NOT NULL AND supplier_name != ''")
    except:
        pass

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_revision_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                revision_job_id VARCHAR(64) NOT NULL,
                rollback_job_id VARCHAR(64) NULL,
                offer_id VARCHAR(64) NOT NULL,
                sku_id VARCHAR(64) NULL,
                entity_type VARCHAR(16) NOT NULL,
                field_name VARCHAR(32) NOT NULL,
                old_value DECIMAL(10,2) NULL,
                new_value DECIMAL(10,2) NULL,
                source_type VARCHAR(32) DEFAULT 'ALIBABA_1688',
                operation_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                rolled_back_at TIMESTAMP NULL,
                INDEX idx_revision_job_id (revision_job_id),
                INDEX idx_offer_id_field_name (offer_id, field_name),
                INDEX idx_sku_id (sku_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating price_revision_log: {e}")

    price_revision_log_columns = [
        ('operator', "VARCHAR(128) DEFAULT ''"),
        ('operation_type', "VARCHAR(64) DEFAULT ''"),
    ]
    for col_name, col_type in price_revision_log_columns:
        try:
            cursor.execute(f'ALTER TABLE price_revision_log ADD COLUMN {col_name} {col_type}')
        except:
            pass

    try:
        cursor.execute('ALTER TABLE price_revision_log ADD INDEX idx_revision_operator (operator)')
    except:
        pass

    try:
        cursor.execute('ALTER TABLE price_revision_log ADD INDEX idx_revision_operation_type (operation_type)')
    except:
        pass

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_adjustment_backup (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                backup_job_id VARCHAR(64) NOT NULL,
                offer_id VARCHAR(64) NOT NULL,
                source_type VARCHAR(32) DEFAULT 'ALIBABA_1688',
                backup_data LONGTEXT NOT NULL,
                operator VARCHAR(128) DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_price_adjustment_backup_item (backup_job_id, offer_id, source_type),
                INDEX idx_price_adjustment_backup_job (backup_job_id),
                INDEX idx_price_adjustment_backup_offer (offer_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating price_adjustment_backup: {e}")

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS import_exclusion_report (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                report_id VARCHAR(64) NOT NULL,
                source_type VARCHAR(32) DEFAULT 'ALIBABA_1688',
                import_task_id VARCHAR(64) DEFAULT '',
                excluded_count INT DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_report_id (report_id),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating import_exclusion_report: {e}")

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS import_exclusion_report_item (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                report_id VARCHAR(64) NOT NULL,
                row_index INT NOT NULL,
                raw_category_name VARCHAR(255) DEFAULT '',
                exclude_reason VARCHAR(255) DEFAULT '',
                masked_title VARCHAR(500) DEFAULT '',
                suggestion VARCHAR(255) DEFAULT '',
                offer_id VARCHAR(64) DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_report_id_row_index (report_id, row_index)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating import_exclusion_report_item: {e}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS erp_category (
            id BIGINT PRIMARY KEY,
            parentId BIGINT NULL,
            name VARCHAR(255) NOT NULL,
            picUrl VARCHAR(500),
            sort INT DEFAULT 0,
            status INT DEFAULT 0,
            description TEXT,
            createTime VARCHAR(64),
            visible TINYINT DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_parent_id (parentId),
            INDEX idx_status (status),
            INDEX idx_name (name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    erp_category_columns = [
        ('parentId', 'BIGINT NULL'),
        ('picUrl', 'VARCHAR(500)'),
        ('sort', 'INT DEFAULT 0'),
        ('status', 'INT DEFAULT 0'),
        ('description', 'TEXT'),
        ('createTime', 'VARCHAR(64)'),
        ('visible', 'TINYINT DEFAULT 1'),
    ]

    for col_name, col_type in erp_category_columns:
        try:
            cursor.execute(f'ALTER TABLE erp_category ADD COLUMN {col_name} {col_type}')
        except:
            pass

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS import_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                offer_id VARCHAR(64) DEFAULT '',
                import_task_id VARCHAR(64) DEFAULT '',
                raw_category_name VARCHAR(255) DEFAULT '',
                reject_reason VARCHAR(255) DEFAULT '',
                suggestion_value VARCHAR(255) DEFAULT '',
                product_title VARCHAR(500) DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_import_log_task_id (import_task_id),
                INDEX idx_import_log_offer_id (offer_id),
                INDEX idx_import_log_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating import_log: {e}")

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS erp_sync_tasks (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                task_no VARCHAR(128) NOT NULL,
                source VARCHAR(32) NOT NULL DEFAULT 'ALIBABA_1688',
                username VARCHAR(128) DEFAULT '',
                tenant_id VARCHAR(64) DEFAULT '',
                access_token VARCHAR(1024) DEFAULT '',
                refresh_token VARCHAR(1024) DEFAULT '',
                expire_time BIGINT DEFAULT 0,
                offer_ids_json MEDIUMTEXT,
                immediate_failed_offer_ids_json TEXT,
                finalized TINYINT NOT NULL DEFAULT 0,
                finalized_at TIMESTAMP NULL DEFAULT NULL,
                last_task_status INT DEFAULT NULL,
                last_task_status_desc VARCHAR(255) DEFAULT '',
                reconcile_error VARCHAR(500) DEFAULT '',
                reconcile_attempt_count INT NOT NULL DEFAULT 0,
                last_reconcile_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_erp_sync_task_no (task_no),
                INDEX idx_erp_sync_finalized_created (finalized, created_at),
                INDEX idx_erp_sync_source (source)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating erp_sync_tasks: {e}")

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ali1688_message_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                message_id VARCHAR(128) NOT NULL,
                event_type VARCHAR(128) NOT NULL,
                member_id VARCHAR(128) DEFAULT '',
                product_ids TEXT,
                payload_json LONGTEXT,
                raw_body LONGTEXT,
                signature_valid TINYINT DEFAULT 0,
                process_status VARCHAR(32) NOT NULL DEFAULT 'received',
                process_result LONGTEXT,
                error_message TEXT,
                attempt_count INT NOT NULL DEFAULT 1,
                first_received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                processed_at TIMESTAMP NULL DEFAULT NULL,
                UNIQUE KEY uniq_ali1688_message_id (message_id),
                INDEX idx_ali1688_msg_event_status (event_type, process_status),
                INDEX idx_ali1688_msg_received_at (first_received_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating ali1688_message_log: {e}")

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ali1688_product_change_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                message_id VARCHAR(128) NOT NULL,
                change_type VARCHAR(32) NOT NULL,
                event_type VARCHAR(128) NOT NULL,
                source_type VARCHAR(32) DEFAULT 'ALIBABA_1688',
                offer_id VARCHAR(64) NOT NULL DEFAULT '',
                sku_id VARCHAR(64) NOT NULL DEFAULT '',
                member_id VARCHAR(128) DEFAULT '',
                detail_text VARCHAR(1000) DEFAULT '',
                detail_json LONGTEXT,
                msg_send_time VARCHAR(64) DEFAULT '',
                occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_ali1688_change_message_item (message_id, change_type, offer_id, sku_id),
                INDEX idx_ali1688_change_type_created (source_type, change_type, occurred_at),
                INDEX idx_ali1688_change_offer_created (source_type, offer_id, occurred_at),
                INDEX idx_ali1688_change_message (message_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating ali1688_product_change_log: {e}")

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ali1688_product_follow_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                offer_id VARCHAR(64) NOT NULL DEFAULT '',
                product_id VARCHAR(64) NOT NULL DEFAULT '',
                source_type VARCHAR(32) DEFAULT 'ALIBABA_1688',
                request_payload LONGTEXT,
                response_payload LONGTEXT,
                status VARCHAR(32) NOT NULL DEFAULT 'failed',
                error_code VARCHAR(128) DEFAULT '',
                error_message TEXT,
                attempt_no INT NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ali1688_follow_offer_created (source_type, offer_id, created_at),
                INDEX idx_ali1688_follow_status_created (source_type, status, created_at),
                INDEX idx_ali1688_follow_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
    except Exception as e:
        print(f"[Init DB] Warning creating ali1688_product_follow_log: {e}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_execution_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(255),
            request_method VARCHAR(32),
            request_path VARCHAR(255),
            request_params TEXT,
            response_data LONGTEXT,
            success TINYINT DEFAULT 1,
            error_message TEXT,
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            execution_time INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_username (username),
            INDEX idx_request_method (request_method),
            INDEX idx_success (success),
            INDEX idx_created_at (created_at),
            INDEX idx_created_at_success (created_at, success),
            INDEX idx_username_created_at (username, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')
    
    # 添加复合索引（如果不存在）
    try:
        cursor.execute('ALTER TABLE task_execution_log ADD INDEX idx_created_at_success (created_at, success)')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE task_execution_log ADD INDEX idx_username_created_at (username, created_at)')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE task_execution_log MODIFY COLUMN response_data LONGTEXT')
    except:
        pass

    conn.commit()
    conn.close()
    print('MySQL database initialized successfully.')


if __name__ == '__main__':
    init_db()
    print('Database initialized successfully.')
