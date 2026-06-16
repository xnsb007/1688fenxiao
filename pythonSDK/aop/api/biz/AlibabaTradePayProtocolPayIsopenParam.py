# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradePayProtocolPayIsopenParam(BaseApi):
    """查询是否开通代扣协议

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.pay.protocolPay.isopen&v=1&cat=payment

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.pay.protocolPay.isopen'

    def get_required_params(self):
        return []

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
