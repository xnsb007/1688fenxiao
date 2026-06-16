# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeCancelParam(BaseApi):
    """买家或者卖家取消交易，注意只有特定状态的交易才能取消，1688可用于取消未付款的订单。

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.cancel&v=1&cat=aop.trade

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.webSite = None
        self.tradeID = None
        self.cancelReason = None
        self.remark = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.cancel'

    def get_required_params(self):
        return ['webSite', 'tradeID', 'cancelReason', 'remark']

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
