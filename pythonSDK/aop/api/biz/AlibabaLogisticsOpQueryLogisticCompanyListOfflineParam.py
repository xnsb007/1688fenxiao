# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaLogisticsOpQueryLogisticCompanyListOfflineParam(BaseApi):
    """查询自己联系物流的物流公司列表

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.logistics&n=alibaba.logistics.OpQueryLogisticCompanyList.offline&v=1&cat=wuliu

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None

    def get_api_uri(self):
        return '1/com.alibaba.logistics/alibaba.logistics.OpQueryLogisticCompanyList.offline'

    def get_required_params(self):
        return []

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
