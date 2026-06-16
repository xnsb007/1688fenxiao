# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeGetBuyerViewParam(BaseApi):
    """获取单个交易明细信息，仅限买家调用。该API需要向阿里巴巴开放平台申请权限才能使用。Get a single transaction detail, only for users to call.

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.get.buyerView&v=1&cat=order_category

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.webSite = None
        self.orderId = None
        self.includeFields = None
        self.attributeKeys = None
        self.outOrderId = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.get.buyerView'

    def get_required_params(self):
        return ['webSite', 'orderId', 'includeFields', 'attributeKeys', 'outOrderId']

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
