# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaCnAlibabaOpenTradeOrderReceiveGoodsParam(BaseApi):
    """${api.doc}


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)

    def get_api_uri(self):
        return '2/cn.alibaba.open/alibaba.cn.alibaba.open.trade.order.receiveGoods'

    def get_required_params(self):
        return []

    def get_multipart_params(self):
        return []

    def need_sign(self):
        return False

    def need_timestamp(self):
        return False

    def need_auth(self):
        return False

    def need_https(self):
        return False

    def is_inner_api(self):
        return False
