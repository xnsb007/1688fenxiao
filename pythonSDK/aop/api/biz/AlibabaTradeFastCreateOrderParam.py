# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaTradeFastCreateOrderParam(BaseApi):
    """快速创建1688大市场订单和1688代销订单，订单一步创建，不需要先调用订单预览，接口参数简单，地址参数传省市区街道的文本名，不需要额外查询地址码，系统默认选择最优惠下单方式，默认支付宝担保交易方式，详细地址必须不超过200个字，不要用地址做其他用途，需要留言或备注的有专门字段，留言和备注都支持500字

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.trade&n=alibaba.trade.fastCreateOrder&v=1&cat=aop.trade

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.flow = None
        self.subUserId = None
        self.message = None
        self.addressParam = None
        self.cargoParamList = None
        self.invoiceParam = None
        self.isvBizTypeStr = None
        self.isvBizTypeErp = None
        self.isvBizTypePD = None
        self.shopPromotionId = None
        self.tradeType = None
        self.encryptOutOrderInfo = None
        self.instanceId = None
        self.proxySettleRecordId = None
        self.fenxiaoChannel = None
        self.outOrderId = None
        self.preSelectPayChannel = None
        self.noUseRedEnvelope = None
        self.useRedEnvelope = None
        self.extendParam = None

    def get_api_uri(self):
        return '1/com.alibaba.trade/alibaba.trade.fastCreateOrder'

    def get_required_params(self):
        return ['flow', 'subUserId', 'message', 'addressParam', 'cargoParamList', 'invoiceParam', 'isvBizTypeStr', 'isvBizTypeErp', 'isvBizTypePD', 'shopPromotionId', 'tradeType', 'encryptOutOrderInfo', 'instanceId', 'proxySettleRecordId', 'fenxiaoChannel', 'outOrderId', 'preSelectPayChannel', 'noUseRedEnvelope', 'useRedEnvelope', 'extendParam']

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
