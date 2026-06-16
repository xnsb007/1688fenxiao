# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeRefundOpQueryOrderRefundOperationListParam(BaseApi):
    """该API为买家使用，卖家查询请使用alibaba.trade.refund.OpQueryOrderRefund.sellerView，买方退款操作记录

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.refund.OpQueryOrderRefundOperationList&v=1&cat=order_refund

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.refundId = None
        self.pageNo = None
        self.pageSize = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.refund.OpQueryOrderRefundOperationList'

    def get_required_params(self):
        return ['refundId', 'pageNo', 'pageSize']

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
