# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeCreateRefundParam(BaseApi):
    """创建退款退货申请

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.createRefund&v=1&cat=trade

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.orderId = None
        self.orderEntryIds = None
        self.disputeRequest = None
        self.applyPayment = None
        self.applyCarriage = None
        self.applyReasonId = None
        self.description = None
        self.goodsStatus = None
        self.vouchers = None
        self.orderEntryCountList = None
        self.customRefund = None
        self.refundRemark = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.createRefund'

    def get_required_params(self):
        return ['orderId', 'orderEntryIds', 'disputeRequest', 'applyPayment', 'applyCarriage', 'applyReasonId', 'description', 'goodsStatus', 'vouchers', 'orderEntryCountList', 'customRefund', 'refundRemark']

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
