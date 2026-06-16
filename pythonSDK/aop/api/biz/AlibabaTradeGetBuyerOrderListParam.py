# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeGetBuyerOrderListParam(BaseApi):
    """获取买家的订单列表，也就是用户的memberId必须等于订单里的买家memberId。该接口仅仅返回订单基本信息，不会返回订单的物流信息和发票信息；如果需要获取物流信息，请调用获取订单详情接口；如果需要获取发票信息，请调用获取发票信息的API

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.getBuyerOrderList&v=1&cat=aop.trade

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.bizTypes = None
        self.createEndTime = None
        self.createStartTime = None
        self.isHis = None
        self.modifyEndTime = None
        self.modifyStartTime = None
        self.orderStatus = None
        self.page = None
        self.pageSize = None
        self.refundStatus = None
        self.sellerMemberId = None
        self.sellerLoginId = None
        self.sellerRateStatus = None
        self.tradeType = None
        self.productName = None
        self.needBuyerAddressAndPhone = None
        self.needMemoInfo = None
        self.outOrderId = None
        self.needInvoicingSetting = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.getBuyerOrderList'

    def get_required_params(self):
        return ['bizTypes', 'createEndTime', 'createStartTime', 'isHis', 'modifyEndTime', 'modifyStartTime', 'orderStatus', 'page', 'pageSize', 'refundStatus', 'sellerMemberId', 'sellerLoginId', 'sellerRateStatus', 'tradeType', 'productName', 'needBuyerAddressAndPhone', 'needMemoInfo', 'outOrderId', 'needInvoicingSetting']

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
