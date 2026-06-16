# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeRefundBuyerQueryOrderRefundListParam(BaseApi):
    """买家查看退款单列表，该接口不支持子账号查询，请使用主账号授权后查询

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.refund.buyer.queryOrderRefundList&v=1&cat=aop.trade

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.orderId = None
        self.applyStartTime = None
        self.applyEndTime = None
        self.refundStatusSet = None
        self.sellerMemberId = None
        self.currentPageNum = None
        self.pageSize = None
        self.logisticsNo = None
        self.modifyStartTime = None
        self.modifyEndTime = None
        self.dipsuteType = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.refund.buyer.queryOrderRefundList'

    def get_required_params(self):
        return ['orderId', 'applyStartTime', 'applyEndTime', 'refundStatusSet', 'sellerMemberId', 'currentPageNum', 'pageSize', 'logisticsNo', 'modifyStartTime', 'modifyEndTime', 'dipsuteType']

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
