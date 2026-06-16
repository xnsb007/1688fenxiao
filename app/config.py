import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 商品来源类型常量
SOURCE_TYPE = 'ALIBABA_1688'  # 统一来源标识

ALI1688_APP_KEY = os.environ.get('ALI1688_APP_KEY', '')
ALI1688_APP_SECRET = os.environ.get('ALI1688_APP_SECRET', '')
ALI1688_ACCESS_TOKEN = os.environ.get('ALI1688_ACCESS_TOKEN', '')
ALI1688_SERVER = 'gw.open.1688.com'
ALI1688_MESSAGE_CALLBACK_SECRET = os.environ.get('ALI1688_MESSAGE_CALLBACK_SECRET', ALI1688_APP_SECRET)
ALI1688_MESSAGE_SIGNATURE_REQUIRED = os.environ.get(
    'ALI1688_MESSAGE_SIGNATURE_REQUIRED',
    '1' if ALI1688_MESSAGE_CALLBACK_SECRET else '0'
).lower() in ('1', 'true', 'yes', 'on')
ALI1688_MESSAGE_MAX_SKEW_SECONDS = int(os.environ.get('ALI1688_MESSAGE_MAX_SKEW_SECONDS', 600))

DINGTALK_CLIENT_ID = os.environ.get('DINGTALK_CLIENT_ID', '')
DINGTALK_CLIENT_SECRET = os.environ.get('DINGTALK_CLIENT_SECRET', '')

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
APP_TIMEZONE = os.environ.get('APP_TIMEZONE', 'Asia/Shanghai')
MYSQL_TIME_ZONE = os.environ.get('MYSQL_TIME_ZONE', '+08:00')

MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))
MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '')
MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', 'fenxiao_selection')

MYSQL_CONFIG = {
    'host': MYSQL_HOST,
    'port': MYSQL_PORT,
    'user': MYSQL_USER,
    'password': MYSQL_PASSWORD,
    'database': MYSQL_DATABASE,
    'charset': 'utf8mb4',
    'connect_timeout': 5,
    'read_timeout': 10,
    'write_timeout': 10
}

# 数据库连接池配置
DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', 20))
DB_POOL_RECYCLE = int(os.environ.get('DB_POOL_RECYCLE', 3600))  # 1小时回收连接

ERP_API_URL = os.environ.get('ERP_API_URL', '')
ERP_API_KEY = os.environ.get('ERP_API_KEY', '')
ERP_API_SECRET = os.environ.get('ERP_API_SECRET', '')
WECOM_ROBOT_WEBHOOK = os.environ.get('WECOM_ROBOT_WEBHOOK', '')
WECOM_ROBOT_KEY = os.environ.get('WECOM_ROBOT_KEY', '')

# 腾讯云COS配置
TENCENT_COS_SECRET_ID = os.environ.get('TENCENT_COS_SECRET_ID', '')
TENCENT_COS_SECRET_KEY = os.environ.get('TENCENT_COS_SECRET_KEY', '')
TENCENT_COS_REGION = os.environ.get('TENCENT_COS_REGION', 'ap-guangzhou')
TENCENT_COS_BUCKET = os.environ.get('TENCENT_COS_BUCKET', '')
TENCENT_COS_APP_ID = os.environ.get('TENCENT_COS_APP_ID', '')
