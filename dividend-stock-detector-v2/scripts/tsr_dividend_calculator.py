#!/usr/bin/env python3
import argparse
import json
import sys

def calculate_tsr_metrics(data):
    """计算 TSR 相关核心指标"""
    industry_type = data.get("industry_type", "general")

    # 单点数据（用于 TSR 计算）
    cfo_avg = data.get("cfo_avg", 0)
    capex_avg = data.get("capex_avg", 0)
    net_profit = data.get("net_profit", 0)
    total_dividend = data.get("total_dividend", 0)
    cancellation_buyback = data.get("cancellation_buyback", 0)
    parent_unallocated_profit = data.get("parent_unallocated_profit", 0)
    parent_cash = data.get("parent_cash", 0)
    market_cap = data.get("market_cap", 1)

    # 时间序列数据（用于 V3 跨期校验）
    historical_data = data.get("historical_data", [])

    # 强周期陷阱参数（用于 V4 判断）
    cyclical_params = data.get("cyclical_params", {})

    # 金融/地产专用参数
    finance_params = data.get("finance_params", {})

    # 总分配额（现金分红 + 注销式回购）
    total_distribution = total_dividend + cancellation_buyback

    # 真实股东收益率（Shareholder Yield）
    shareholder_yield = (total_distribution / market_cap) * 100 if market_cap > 0 else 0

    # 根据行业类型计算真实自由现金流
    real_fcf = None
    if industry_type == "general":
        real_fcf = cfo_avg - capex_avg
    elif industry_type == "real_estate":
        # 地产版：扣除预售资金监管受限部分和刚性拿地支出
        restricted_pre_sale_funds = data.get("restricted_pre_sale_funds", 0)
        mandatory_land_payment = data.get("mandatory_land_payment", 0)
        real_fcf = cfo_avg - restricted_pre_sale_funds - mandatory_land_payment
    elif industry_type == "finance":
        # 金融业不适用 FCF 模型
        real_fcf = None
    else:
        real_fcf = cfo_avg - capex_avg

    # TSR 覆盖率（仅适用于非金融业）
    tsr_coverage = None
    if real_fcf is not None and total_distribution > 0:
        tsr_coverage = real_fcf / total_distribution

    # 母公司现金流转摩擦率
    parent_cash_friction = None
    if parent_cash > 0:
        parent_cash_friction = total_distribution / parent_cash

    # 合规性校验
    compliance_status = {
        "parent_unallocated_positive": parent_unallocated_profit > 0,
        "parent_cash_sufficient": total_distribution <= parent_cash if parent_cash > 0 else False,
        "dividend_cap_ratio": (total_distribution / net_profit * 100) if net_profit > 0 else None
    }

    # 生成警告信息（包含 V5 金融规则、V3 跨期校验、V4 周期陷阱）
    warnings = generate_warnings(
        tsr_coverage, parent_cash_friction, compliance_status,
        total_distribution, net_profit, shareholder_yield,
        historical_data, cyclical_params, finance_params, industry_type
    )

    return {
        "status": "success",
        "industry_type": industry_type,
        "metrics": {
            "total_distribution": total_distribution,
            "shareholder_yield": round(shareholder_yield, 2),
            "real_fcf": round(real_fcf, 2) if real_fcf is not None else None,
            "tsr_coverage": round(tsr_coverage, 4) if tsr_coverage is not None else None,
            "parent_cash_friction": round(parent_cash_friction, 4) if parent_cash_friction is not None else None
        },
        "compliance": compliance_status,
        "warnings": warnings
    }

def generate_warnings(tsr_coverage, parent_cash_friction, compliance, total_dist, net_profit,
                      shareholder_yield, historical_data, cyclical_params, finance_params, industry_type):
    """生成警告信息"""
    warnings = []

    # V1: 母公司分配能力双杀
    if not compliance["parent_unallocated_positive"]:
        warnings.append({
            "type": "VETO",
            "code": "V1",
            "message": "母公司单体未分配利润 ≤ 0，分配能力存疑"
        })

    if compliance["parent_cash_sufficient"] == False and parent_cash_friction is not None:
        if parent_cash_friction > 1:
            warnings.append({
                "type": "VETO",
                "code": "V1",
                "message": f"母公司现金流转摩擦率 {parent_cash_friction:.2f} > 1，资金可能被困在子公司"
            })

    # V2: TSR 覆盖率过低（结合有息负债增长判断）
    debt_yoy_growth = finance_params.get("debt_yoy_growth", 0)
    if tsr_coverage is not None and tsr_coverage < 0.5 and debt_yoy_growth > 30:
        warnings.append({
            "type": "VETO",
            "code": "V2",
            "message": f"TSR 覆盖率 {tsr_coverage:.2f} < 0.5 且有息负债同比增长 {debt_yoy_growth}%，存在借贷分红嫌疑"
        })

    # V3: 利润表严重透支（跨期校验：连续 2 年 > 120%）
    if historical_data and len(historical_data) >= 2:
        consecutive_over_dividend = 0
        for year_data in historical_data[-2:]:
            year_profit = year_data.get("net_profit", 0)
            year_dist = year_data.get("total_distribution", 0)
            if year_profit > 0 and (year_dist / year_profit) > 1.2:
                consecutive_over_dividend += 1

        if consecutive_over_dividend >= 2:
            has_oneoff_gain = finance_params.get("has_oneoff_gain", False)
            if not has_oneoff_gain:
                warnings.append({
                    "type": "VETO",
                    "code": "V3",
                    "message": "总分配额连续 2 年 > 归母净利润的 120% 且无重大资产出售，利润表严重透支"
                })
    else:
        # 降级：仅提供单年数据时的 WARNING
        if compliance["dividend_cap_ratio"] is not None and compliance["dividend_cap_ratio"] > 120:
            warnings.append({
                "type": "WARNING",
                "code": "V3",
                "message": f"单年总分配额占归母净利润 {compliance['dividend_cap_ratio']:.1f}% > 120%，需结合历史数据判断是否透支"
            })

    # V4: 强周期极值陷阱（硬逻辑判断）
    is_cyclical = cyclical_params.get("is_cyclical", False)
    if is_cyclical:
        profit_10y_percentile = cyclical_params.get("profit_10y_percentile", 0)
        product_price_decline = cyclical_params.get("product_price_decline", 0)

        if profit_10y_percentile >= 0.8 and product_price_decline > 0.2:
            warnings.append({
                "type": "VETO",
                "code": "V4",
                "message": f"强周期行业利润处近 10 年 {profit_10y_percentile:.0%} 高位，且产品价格回撤 {product_price_decline:.0%}，触发周期极值陷阱"
            })

    # V5: 监管红线触底（金融专用）
    if industry_type == "finance":
        # 银行
        if finance_params.get("sector") == "bank":
            cet1_ratio = finance_params.get("cet1_ratio", 0)
            regulatory_cet1 = finance_params.get("regulatory_cet1_requirement", 7.5)
            safety_margin = cet1_ratio - regulatory_cet1
            if safety_margin < 1.5:
                warnings.append({
                    "type": "VETO",
                    "code": "V5",
                    "message": f"银行核心一级资本充足率安全边际 {safety_margin:.2f}% < 1.5%，逼近监管红线，未来必须削减分红"
                })
        # 保险
        elif finance_params.get("sector") == "insurance":
            solvency_ratio = finance_params.get("solvency_ratio", 0)
            if solvency_ratio < 150:
                warnings.append({
                    "type": "VETO",
                    "code": "V5",
                    "message": f"保险偿付能力充足率 {solvency_ratio}% < 150%，触发监管预警，分红能力受限"
                })
        # 证券
        elif finance_params.get("sector") == "securities":
            net_capital_ratio = finance_params.get("net_capital_ratio", 0)
            if net_capital_ratio < 120:
                warnings.append({
                    "type": "VETO",
                    "code": "V5",
                    "message": f"证券净资本比率 {net_capital_ratio}% < 120%，监管趋严，分红需谨慎"
                })

    # W4: 异常畸高收益率
    if shareholder_yield > 15:
        profit_decline = finance_params.get("profit_yoy_decline", 0)
        revenue_decline = finance_params.get("revenue_yoy_decline", 0)
        if profit_decline > 0.2 and revenue_decline > 0.1:
            warnings.append({
                "type": "WARNING",
                "code": "W4",
                "message": f"真实股东收益率 {shareholder_yield:.2f}% > 15% 且基本面恶化（利润降 {profit_decline:.0%}，营收降 {revenue_decline:.0%}），需警惕被动高息"
            })
        else:
            warnings.append({
                "type": "WARNING",
                "code": "W4",
                "message": f"真实股东收益率 {shareholder_yield:.2f}% > 15%，需验证基本面是否恶化"
            })

    return warnings

def main():
    parser = argparse.ArgumentParser(description="TSR 红利股计算引擎")
    parser.add_argument("--data", required=True, help="JSON 格式的财务数据")
    args = parser.parse_args()

    try:
        data = json.loads(args.data)
        result = calculate_tsr_metrics(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"JSON 解析失败: {str(e)}"}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"计算失败: {str(e)}"}), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
