# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class FenxiaoOrderCreateReverseOrderParam(BaseApi):
    """分销订单回传售后信息，ISV侧根据订单是否发生售后来回传售后相关信息


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.createReq = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/fenxiao.order.createReverseOrder'

    def get_required_params(self):
        return ['createReq']

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
