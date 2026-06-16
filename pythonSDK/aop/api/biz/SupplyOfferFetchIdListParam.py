# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class SupplyOfferFetchIdListParam(BaseApi):
    """分批拉取商品——仅获取商品Id


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.taskId = None
        self.batchNo = None
        self.startIndex = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/supply.offer.fetchIdList'

    def get_required_params(self):
        return ['taskId', 'batchNo', 'startIndex']

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
