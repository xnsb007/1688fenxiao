# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaCreateOrderPreviewParam(BaseApi):
    """订单创建只允许购买同一个供应商的商品。本接口返回创建订单相关的优惠等信息。
1、校验商品数据是否允许订购。
2、校验代销关系
3、校验库存、起批量、是否满足混批条件

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.createOrder.preview&v=1&cat=order_category

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.addressParam = None
        self.cargoParamList = None
        self.invoiceParam = None
        self.flow = None
        self.instanceId = None
        self.encryptOutOrderInfo = None
        self.proxySettleRecordId = None
        self.inventoryMode = None
        self.outOrderId = None
        self.pickupService = None
        self.crossBorderLogisticsSolutionId = None
        self.useBorderLogisticsSolution = None
        self.isvBizType = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.createOrder.preview'

    def get_required_params(self):
        return ['addressParam', 'cargoParamList', 'invoiceParam', 'flow', 'instanceId', 'encryptOutOrderInfo', 'proxySettleRecordId', 'inventoryMode', 'outOrderId', 'pickupService', 'crossBorderLogisticsSolutionId', 'useBorderLogisticsSolution', 'isvBizType']

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
