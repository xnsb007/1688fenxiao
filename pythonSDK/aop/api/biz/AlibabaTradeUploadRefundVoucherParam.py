# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeUploadRefundVoucherParam(BaseApi):
    """上传退款退货凭证，用于退款退货申请，文件流转byte数组推荐使用org.apache.commons.io.IOUtils#toByteArray(java.io.InputStream)

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.uploadRefundVoucher&v=1&cat=trade

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.imageData = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.uploadRefundVoucher'

    def get_required_params(self):
        return ['imageData']

    def get_multipart_params(self):
        return ['imageData']

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
