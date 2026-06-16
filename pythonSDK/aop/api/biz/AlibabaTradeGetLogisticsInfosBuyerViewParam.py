# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeGetLogisticsInfosBuyerViewParam(BaseApi):
    """该接口需要获得订单买家的授权，获取买家的订单的物流详情，在采购或者分销场景中，作为买家也有获取物流详情的需求。该接口能查能根据订单号查看物流详情，包括发件人，收件人，所发货物明细等。由于物流单录入的原因，可能跟踪信息的API查询会有延迟。该API需要向开放平台申请权限才能访问。In the procurement or distribution scenario, buyers can ask for obtaining the logistics details. The interface can check the logistics details according to the order ID, including the sender, the recipient, the details of the goods sent, and so on. Depending on the logistics information entry time, there may be a delay in API queries regarding the information tracking.

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.logistics&n=alibaba.trade.getLogisticsInfos.buyerView&v=1&cat=wuliu

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.orderId = None
        self.fields = None
        self.webSite = None

    def get_api_uri(self):
        return '1/com.alibaba.logistics/alibaba.trade.getLogisticsInfos.buyerView'

    def get_required_params(self):
        return ['orderId', 'fields', 'webSite']

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
