from flask import Blueprint, request, jsonify
from app.services.dingtalk_service import dingtalk_service
import json

dingtalk_bp = Blueprint('dingtalk', __name__)

@dingtalk_bp.route('/dingtalk/callback', methods=['POST'])
def dingtalk_callback():
    """
    钉钉机器人回调接口
    用于接收钉钉群聊中的消息
    """
    try:
        data = request.get_json()
        
        # 钉钉回调验证
        if data.get('msg_signature'):
            # 处理加密消息（生产环境需要解密）
            pass
        
        # 获取消息内容
        msg_type = data.get('msgtype', 'text')
        
        if msg_type == 'text':
            content = data.get('text', {}).get('content', '').strip()
        else:
            return jsonify({
                'msgtype': 'text',
                'text': {'content': '暂不支持该消息类型，请发送文字消息'}
            })
        
        # 处理消息
        sender_id = data.get('senderStaffId', '')
        result = dingtalk_service.handle_message(content, sender_id)
        
        # 返回钉钉机器人响应格式
        return jsonify({
            'msgtype': 'text',
            'text': {'content': result['message']}
        })
        
    except Exception as e:
        return jsonify({
            'msgtype': 'text',
            'text': {'content': f'处理消息时出错: {str(e)}'}
        })

@dingtalk_bp.route('/dingtalk/webhook', methods=['POST'])
def dingtalk_webhook():
    """
    钉钉Outgoing机器人Webhook接口
    用于接收@机器人的消息
    """
    try:
        data = request.get_json()
        
        # 获取被@的消息内容
        content = data.get('text', {}).get('content', '').strip()
        
        # 移除@机器人的部分
        content = content.split(']')[-1].strip() if ']' in content else content
        
        sender_id = data.get('senderStaffId', '')
        result = dingtalk_service.handle_message(content, sender_id)
        
        return jsonify({
            'msgtype': 'text',
            'text': {'content': result['message']}
        })
        
    except Exception as e:
        return jsonify({
            'msgtype': 'text',
            'text': {'content': f'处理消息时出错: {str(e)}'}
        })
