# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeAddresscodeGetParam(BaseApi):
    """获取交易地址代码表，该API会返回输入code的详情和该code的下一级地区code.

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.addresscode.get&v=1&cat=trade.address

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.areaCode = None
        self.webSite = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.addresscode.get'

    def get_required_params(self):
        return ['areaCode', 'webSite']

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
