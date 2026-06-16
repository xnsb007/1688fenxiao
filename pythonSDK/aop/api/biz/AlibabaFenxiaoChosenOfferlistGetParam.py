# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaFenxiaoChosenOfferlistGetParam(BaseApi):
    """铺货offer列表获取接口，在分销场景和精选货源场景，分别有选品的功能页，分销商在完成选品后，会跳转到铺货工具，并带入dkey参数，铺货工具获取dkey，并传入该接口参数uniqueKey，可获取具体选品的offer列表和铺货入口（entrance_1688:分销铺货入口  entrance_pft:精选货源铺货入口）。铺货工具需要根据铺货入口的不同，向下游铺货，并记录铺货入口，并在订单回流时，分销场景入口即entrance_1688入口采用fenxiao的交易flow下单，精选货源场景入口即entrance_pft入口采用ttpft的交易flow下单，完成订单的回流。

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.fenxiao&n=alibaba.fenxiao.chosen.offerlist.get&v=1&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.uniqueKey = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/alibaba.fenxiao.chosen.offerlist.get'

    def get_required_params(self):
        return ['uniqueKey']

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
