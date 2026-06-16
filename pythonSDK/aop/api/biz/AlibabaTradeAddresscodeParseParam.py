# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeAddresscodeParseParam(BaseApi):
    """根据地址信息，解析地区码

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.addresscode.parse&v=1&cat=trade.address

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.addressInfo = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.addresscode.parse'

    def get_required_params(self):
        return ['addressInfo']

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
