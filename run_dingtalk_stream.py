#!/usr/bin/env python3
"""
钉钉机器人Stream模式启动脚本
使用钉钉Stream SDK连接钉钉服务器
"""
import os
import sys
import time
import asyncio

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 添加项目路径
sys.path.insert(0, BASE_DIR)

from runtime_logging import setup_daily_file_logging

setup_daily_file_logging('dingtalk', log_dir=os.path.join(BASE_DIR, 'logs'))

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
        print(f'✓ Loaded config from {env_file}')
    except Exception as e:
        print(f'⚠ Error loading config: {e}')
        # 尝试不带encoding参数
        try:
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key] = value.strip()
            print(f'✓ Loaded config from {env_file} (without encoding)')
        except Exception as e2:
            print(f'⚠ Failed to load config: {e2}')
else:
    print(f'⚠ Config file not found in any of: {possible_paths}')

setup_daily_file_logging('dingtalk', log_dir=os.path.join(BASE_DIR, 'logs'))

from app.config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET, BASE_URL
from app.services.dingtalk_service import dingtalk_service

def main():
    print("=" * 60)
    print("钉钉机器人Stream模式")
    print("=" * 60)
    
    # 检查配置
    if not DINGTALK_CLIENT_ID or not DINGTALK_CLIENT_SECRET:
        print("\n❌ 错误: 未配置钉钉凭证")
        print("\n请设置以下环境变量:")
        print("  export DINGTALK_CLIENT_ID='your_client_id'")
        print("  export DINGTALK_CLIENT_SECRET='your_client_secret'")
        print("\n获取方式:")
        print("  1. 登录钉钉开放平台: https://open.dingtalk.com/")
        print("  2. 创建企业内部应用")
        print("  3. 在'凭证与基础信息'中获取Client ID和Client Secret")
        return
    
    print(f"\n✓ Client ID: {DINGTALK_CLIENT_ID[:15]}...")
    print(f"✓ Base URL: {BASE_URL}")
    
    # 尝试导入钉钉Stream SDK
    try:
        from dingtalk_stream import AckMessage
        from dingtalk_stream import ChatbotMessage
        from dingtalk_stream import DingTalkStreamClient
        from dingtalk_stream import Credential
        from dingtalk_stream import ChatbotHandler
        
        print("\n✓ 钉钉Stream SDK已安装")
        
        # 定义消息处理器
        class MyChatbotHandler(ChatbotHandler):
            def handle(self, message: ChatbotMessage):
                content = message.text.content.strip()
                sender_id = message.sender_staff_id
                
                print(f"\n收到消息: {content}")
                print(f"发送者: {sender_id}")
                
                # 处理消息
                result = dingtalk_service.handle_message(content, sender_id)
                
                # 返回文本回复
                self.reply_text(result['message'], message)
                
                # 返回回复状态
                return AckMessage.STATUS_OK
        
        # 创建凭证
        credential = Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET)
        
        # 创建Stream客户端
        client = DingTalkStreamClient(credential)
        
        # 注册消息处理器
        client.register_callback_handler(ChatbotMessage.TOPIC, MyChatbotHandler())
        
        print("\n🚀 启动Stream连接...")
        print("按 Ctrl+C 停止服务\n")
        
        # 启动服务（异步）
        asyncio.run(client.start())
        
    except ImportError:
        print("\n⚠️ 钉钉Stream SDK未安装")
        print("\n安装命令:")
        print("  pip install dingtalk-stream")
        print("\n或者使用简化版HTTP模式:")
        print("  1. 配置Webhook地址: https://fenxiao.1bgo.com/dingtalk/webhook")
        print("  2. 使用HTTP回调接收消息")
        
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 服务已停止")
