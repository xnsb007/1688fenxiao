# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradePayProtocolPayPreparePayParam(BaseApi):
    """发起免密支付，会自动判断是否开通了支付宝或者诚E赊的免密支付，并发起扣款。优先发起诚E赊自动扣款，如果失败，则尝试支付宝自动扣款。该接口目前返回错误码不详，在发起扣款失败后，建议重试3次，不要无限制重试。

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.pay.protocolPay.preparePay&v=1&cat=payment

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.tradeWithholdPreparePayParam = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.pay.protocolPay.preparePay'

    def get_required_params(self):
        return ['tradeWithholdPreparePayParam']

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
