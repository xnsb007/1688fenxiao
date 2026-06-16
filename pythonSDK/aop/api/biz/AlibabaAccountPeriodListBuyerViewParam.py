# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaAccountPeriodListBuyerViewParam(BaseApi):
    """买家维度查看所有获得的账期授信。可翻页查询，每次返回不超过10条

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.accountPeriod.list.buyerView&v=1&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.pageIndex = None
        self.sellerLoginId = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.accountPeriod.list.buyerView'

    def get_required_params(self):
        return ['pageIndex', 'sellerLoginId']

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
