import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from runtime_logging import setup_daily_file_logging

setup_daily_file_logging('web', log_dir=os.path.join(BASE_DIR, 'logs'))

# 加载环境变量配置文件
# 尝试多个可能的路径
possible_paths = [
    os.path.join(BASE_DIR, 'config', 'production.env'),
    '/opt/fenxiao/config/production.env',
    os.path.join(os.getcwd(), 'config', 'production.env'),
]

env_file = None
for path in possible_paths:
    if os.path.exists(path):
        env_file = path
        break

if env_file:
    try:
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value.strip()
        print(f'Loaded config from {env_file}')
    except Exception as e:
        print(f'Error loading config: {e}')
        # 尝试不带encoding参数
        try:
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key] = value.strip()
            print(f'Loaded config from {env_file} (without encoding)')
        except Exception as e2:
            print(f'Failed to load config: {e2}')
else:
    print(f'Config file not found in any of: {possible_paths}')

setup_daily_file_logging('web', log_dir=os.path.join(BASE_DIR, 'logs'))

sys.path.insert(0, os.path.join(BASE_DIR, 'pythonSDK'))

from app import create_app
from app.models import init_db

init_db()

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
