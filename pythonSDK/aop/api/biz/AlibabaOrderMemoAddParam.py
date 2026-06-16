# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaOrderMemoAddParam(BaseApi):
    """授权用户为卖家修改卖家备忘，授权用户为买家修改买家备忘
注意：该接口可重复调用，备注内容将覆盖前一次调用

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.order.memoAdd&v=1&cat=aop.trade

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.orderId = None
        self.memo = None
        self.remarkIcon = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.order.memoAdd'

    def get_required_params(self):
        return ['orderId', 'memo', 'remarkIcon']

    def get_multipart_params(self):
        return []

    def need_sign(self):
        return True

    def need_timestamp(self):
        return False

    def need_auth(self):
        return True

    def need_https(self):
        return False

    def is_inner_api(self):
        return False
