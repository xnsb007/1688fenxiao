import logging
from typing import Any, Dict

import requests

from app.config import WECOM_ROBOT_KEY, WECOM_ROBOT_WEBHOOK


logger = logging.getLogger(__name__)


class WecomRobotService:
    def __init__(self):
        self.webhook = str(WECOM_ROBOT_WEBHOOK or '').strip()
        self.key = str(WECOM_ROBOT_KEY or '').strip()

    def send_import_exclusion_alert(self, payload: Dict[str, Any]) -> None:
        webhook = self._resolve_webhook()
        if not webhook:
            logger.info('Skip WeCom alert because webhook is not configured')
            return

        count = int(payload.get('excluded_count') or 0)
        if count <= 0:
            return

        report_url = str(payload.get('report_url') or '').strip()
        import_task_id = str(payload.get('import_task_id') or '-').strip() or '-'
        message = (
            f"商品导入排除告警\n"
            f"排除行数：{count}\n"
            f"导入任务：{import_task_id}\n"
            f"报告链接：{report_url or '-'}"
        )
        try:
            requests.post(
                webhook,
                json={'msgtype': 'text', 'text': {'content': message}},
                timeout=10
            )
        except Exception:
            logger.exception('Send WeCom import exclusion alert failed')

    def _resolve_webhook(self) -> str:
        if self.webhook:
            return self.webhook
        if self.key:
            return f'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={self.key}'
        return ''


wecom_robot_service = WecomRobotService()
