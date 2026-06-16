# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeRefundReturnGoodsParam(BaseApi):
    """买家申请退货退款时，卖家同意后，买家提交退款货信息使用，需要先调用alibaba.logistics.OpQueryLogisticCompanyList.offline查询物流公司信息，使用接口返回的物流公司编码

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.refund.returnGoods&v=1&cat=order_category

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.refundId = None
        self.logisticsCompanyNo = None
        self.freightBill = None
        self.description = None
        self.vouchers = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.refund.returnGoods'

    def get_required_params(self):
        return ['refundId', 'logisticsCompanyNo', 'freightBill', 'description', 'vouchers']

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
