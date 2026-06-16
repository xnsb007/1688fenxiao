# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeCancelRefundParam(BaseApi):
    """取消退款退货申请 


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.refundId = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.cancelRefund'

    def get_required_params(self):
        return ['refundId']

    def get_multipart_params(self):
        return []

    def need_sign(self):
        return True

    def need_timestamp(self):
        return False

    def need_auth(self):
        return False

    def need_https(self):
        return False

    def is_inner_api(self):
        return False
