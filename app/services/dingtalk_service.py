import re
import json
import threading
from app.config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET
from app.services.ali1688_service import ali1688_service

class DingTalkService:
    def __init__(self):
        self.client_id = DINGTALK_CLIENT_ID
        self.client_secret = DINGTALK_CLIENT_SECRET
    
    def parse_search_command(self, message):
        patterns = [
            (r'从1688.*?找(\d+)件(.+)', lambda m: (int(m.group(1)), m.group(2).strip())),
            (r'找(\d+)件(.+)', lambda m: (int(m.group(1)), m.group(2).strip())),
            (r'搜索(.+?)(?:\s+(\d+)件)?$', lambda m: (int(m.group(2)) if m.group(2) else 100, m.group(1).strip())),
            (r'帮我找(.+?)(?:\s+(\d+)件)?$', lambda m: (int(m.group(2)) if m.group(2) else 100, m.group(1).strip())),
            (r'采集(\d+)件(.+)', lambda m: (int(m.group(1)), m.group(2).strip())),
            (r'(.+?)(?:\s+(\d+)件)?$', lambda m: (int(m.group(2)) if m.group(2) else 100, m.group(1).strip())),
        ]
        
        for pattern, extractor in patterns:
            match = re.search(pattern, message)
            if match:
                try:
                    quantity, keyword = extractor(match)
                    if keyword and len(keyword) > 1:
                        return {'keyword': keyword, 'quantity': quantity}
                except:
                    continue
        
        return None
    
    def handle_message(self, message_content, sender_id=None):
        command = self.parse_search_command(message_content)
        
        if command:
            result = ali1688_service.search_for_selection(
                keyword=command['keyword'],
                quantity=command['quantity']
            )
            
            if result['success']:
                return {
                    'success': True,
                    'message': f"已为您搜索到{result['total']}件【{command['keyword']}】商品",
                    'total': result['total']
                }
            else:
                return {
                    'success': False,
                    'message': f"搜索失败：{result.get('error', '未知错误')}"
                }
        
        return {
            'success': False,
            'message': '未能识别您的指令，请尝试：\n"从1688分销平台给我找100件羽绒服"'
        }
    
    def start_stream_client(self, on_message_callback=None):
        try:
            from dingtalk_stream import AckMessage
            import dingtalk_stream
            
            def process_message(message):
                content = json.loads(message.data.content)
                text = content.get('content', '').strip()
                sender = message.data.sender_id
                
                result = self.handle_message(text, sender)
                
                if on_message_callback:
                    on_message_callback(result)
                
                return AckMessage.SUCCESS
            
            client = dingtalk_stream.DingTalkStreamClient(
                self.client_id, self.client_secret
            )
            client.register_callback_handler(dingtalk_stream.ChatMessageHandler, process_message)
            
            client.start()
            
        except ImportError:
            print("dingtalk-stream not installed, running in mock mode")
            self._run_mock_mode(on_message_callback)
    
    def _run_mock_mode(self, on_message_callback=None):
        print("DingTalk mock mode - type 'quit' to exit")
        while True:
            try:
                message = input("输入消息: ").strip()
                if message.lower() == 'quit':
                    break
                
                result = self.handle_message(message)
                print(f"回复: {result['message']}")
                
                if on_message_callback:
                    on_message_callback(result)
                    
            except EOFError:
                break

dingtalk_service = DingTalkService()
