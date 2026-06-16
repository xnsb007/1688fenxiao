# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class SupplyRecommendChangeOfferStartTaskParam(BaseApi):
    """开启需换供品池获取任务，开启后通过supply.offer.fetchIdList接口拉取商品列表，最后通过supply.task.stop关闭任务


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.concurrency = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/supply.recommendChangeOffer.startTask'

    def get_required_params(self):
        return ['concurrency']

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
