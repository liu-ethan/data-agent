from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user

router = APIRouter()

QUESTIONS = [
    "上个月 GMV 最高的 5 个渠道是什么？",
    "最近 30 天每天的订单量和 GMV 趋势如何？",
    "哪些商品品类的退款率最高？",
    "各城市的新用户注册数排名如何？",
    "最近一个季度客单价最高的商品类别是什么？",
    "不同支付方式的支付成功率是多少？",
    "活动期间各渠道转化率有什么变化？",
    "哪些商品销量高但利润率低？",
    "本月退款金额最高的原因有哪些？",
    "华东地区最近 7 天的订单趋势如何？",
    "新用户和老用户的消费金额差异如何？",
    "各渠道访问到下单的转化率是多少？",
    "最近 90 天复购用户贡献了多少 GMV？",
    "哪些城市订单量增长最快？",
    "本月退款率相比上月是否升高？",
]


@router.get("/examples")
def get_examples(
    _user: Annotated[dict, Depends(get_current_user)],
):
    return {
        "examples": [
            {"id": str(index), "question": question}
            for index, question in enumerate(QUESTIONS, start=1)
        ]
    }
