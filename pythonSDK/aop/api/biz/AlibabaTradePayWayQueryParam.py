# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradePayWayQueryParam(BaseApi):
    """查询未支付订单可以使用的支付方式或者支付渠道

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.payWay.query&v=1&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.orderId = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.payWay.query'

    def get_required_params(self):
        return ['orderId']

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
