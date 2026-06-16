# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class LogisticsDeliveryUrgeParam(BaseApi):
    """催卖家发货，催发货的订单状态必须为未发货之前。24小时内限制最多一次


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.orderId = None

    def get_api_uri(self):
        return '1/com.alibaba.logistics/logistics.delivery.urge'

    def get_required_params(self):
        return ['orderId']

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
