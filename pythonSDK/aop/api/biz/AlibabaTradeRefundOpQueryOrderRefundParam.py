# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeRefundOpQueryOrderRefundParam(BaseApi):
    """该API为买家使用，卖家查询请使用alibaba.trade.refund.OpQueryOrderRefund.sellerView，查询退款单详情，同时可以查询到退款操作列表。 该API需要向阿里巴巴申请权限才能访问。

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.refund.OpQueryOrderRefund&v=1&cat=order_refund

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.refundId = None
        self.needTimeOutInfo = None
        self.needOrderRefundOperation = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.refund.OpQueryOrderRefund'

    def get_required_params(self):
        return ['refundId', 'needTimeOutInfo', 'needOrderRefundOperation']

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
