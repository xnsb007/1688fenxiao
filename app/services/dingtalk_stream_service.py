"""
钉钉Stream模式服务
用于接收钉钉群聊消息并自动回复
"""
import json
import time
import threading
from app.services.dingtalk_service import dingtalk_service
from app.config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET, BASE_URL

class DingTalkStreamService:
    """钉钉Stream模式服务"""
    
    def __init__(self):
        self.client_id = DINGTALK_CLIENT_ID
        self.client_secret = DINGTALK_CLIENT_SECRET
        self.base_url = BASE_URL
        self.running = False
        
    def start(self):
        """启动Stream服务"""
        if not self.client_id or not self.client_secret:
            print("错误: 未配置钉钉Client ID和Client Secret")
            print("请在环境变量中设置 DINGTALK_CLIENT_ID 和 DINGTALK_CLIENT_SECRET")
            return False
            
        self.running = True
        print(f"钉钉Stream服务启动中...")
        print(f"Client ID: {self.client_id[:10]}...")
        print(f"Base URL: {self.base_url}")
        
        # 启动消息处理线程
        self.thread = threading.Thread(target=self._run_stream)
        self.thread.daemon = True
        self.thread.start()
        
        return True
        
    def _run_stream(self):
        """运行Stream连接"""
        try:
            # 这里应该使用钉钉官方的Stream SDK
            # 由于Stream模式需要特定的SDK，我们使用简化版实现
            print("Stream连接已建立，等待消息...")
            
            # 模拟Stream模式 - 实际部署时需要使用钉钉官方SDK
            # pip install dingtalk-stream
            
            while self.running:
                time.sleep(1)
                
        except Exception as e:
            print(f"Stream错误: {e}")
            
    def stop(self):
        """停止服务"""
        self.running = False
        print("钉钉Stream服务已停止")
        
    def handle_message(self, message_data):
        """处理收到的消息"""
        try:
            # 解析消息
            msg_type = message_data.get('msgtype', 'text')
            
            if msg_type == 'text':
                content = message_data.get('text', {}).get('content', '').strip()
                sender_id = message_data.get('senderStaffId', '')
                
                # 调用业务逻辑处理
                result = dingtalk_service.handle_message(content, sender_id)
                
                return {
                    'msgtype': 'text',
                    'text': {'content': result['message']}
                }
            else:
                return {
                    'msgtype': 'text',
                    'text': {'content': '暂不支持该消息类型，请发送文字消息'}
                }
                
        except Exception as e:
            return {
                'msgtype': 'text',
                'text': {'content': f'处理消息时出错: {str(e)}'}
            }

# 全局实例
dingtalk_stream_service = DingTalkStreamService()
