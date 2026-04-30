#!/usr/bin/env python3
import argparse
import json
import sys

def detect_cycle_position(data):
    """周期位置定位系统"""
    industry = data.get("industry", "port")

    # 四维度评分：每个维度 0-100 分，越高越接近周期顶部
    scores = {}

    # 维度1: 吞吐量/产量增速趋势
    throughput_data = data.get("throughput_data", [])
    throughput_score = calc_trend_score(throughput_data)
    scores["throughput_trend"] = throughput_score

    # 维度2: 运价/产品价格指数趋势
    freight_data = data.get("freight_index_data", [])
    freight_score = calc_trend_score(freight_data)
    scores["freight_trend"] = freight_score

    # 维度3: 产能利用率
    capacity_util = data.get("capacity_utilization", 0.5)
    util_score = min(100, max(0, (capacity_util - 0.5) / 0.5 * 100))
    scores["capacity_utilization"] = round(util_score, 1)

    # 维度4: CAPEX/折旧比
    capex_dep_ratio = data.get("capex_depreciation_ratio", 1.0)
    capex_score = min(100, max(0, (capex_dep_ratio - 0.8) / 2.2 * 100))
    scores["capex_depreciation_ratio"] = round(capex_score, 1)

    # 综合评分 (加权平均)
    weights = {
        "throughput_trend": 0.30,
        "freight_trend": 0.30,
        "capacity_utilization": 0.25,
        "capex_depreciation_ratio": 0.15
    }
    composite = sum(scores[k] * weights[k] for k in scores)

    # 周期位置判定
    if composite < 20:
        position = "trough"
        position_cn = "低谷期"
        description = "行业处于周期底部，利润低迷，但分红韧性强的标的具备逆向配置价值"
    elif composite < 40:
        position = "recovery"
        position_cn = "爬坡期"
        description = "行业从底部回升，吞吐量/运价开始改善，是最佳红利配置窗口"
    elif composite < 60:
        position = "expansion"
        position_cn = "繁荣期"
        description = "行业景气度良好，利润增长，分红可持续性强，但需警惕过热信号"
    elif composite < 80:
        position = "peak"
        position_cn = "见顶期"
        description = "行业接近周期顶部，利润处高位但增速放缓，需做好下行防御"
    else:
        position = "downturn"
        position_cn = "下行期"
        description = "行业进入下行周期，利润下滑风险大，高分红可能不可持续"

    return {
        "composite_score": round(composite, 1),
        "dimension_scores": scores,
        "position": position,
        "position_cn": position_cn,
        "description": description
    }

def calc_trend_score(series):
    """
    计算时间序列的趋势评分
    逻辑：近期数据与远期数据的斜率对比
    返回 0-100 分，越高表示越接近顶部
    """
    if not series or len(series) < 4:
        return 50  # 数据不足时返回中性分数

    values = [float(v) for v in series]
    n = len(values)

    # 计算整体趋势（最近1/3 vs 最初1/3）
    third = max(1, n // 3)
    early_avg = sum(values[:third]) / third
    recent_avg = sum(values[-third:]) / third

    if early_avg == 0:
        return 50

    change_rate = (recent_avg - early_avg) / abs(early_avg)

    # 计算近期斜率（最近3个点）
    if n >= 3:
        recent_slope = values[-1] - values[-3]
        recent_momentum = recent_slope / max(abs(values[-3]), 0.001)
    else:
        recent_momentum = 0

    # 评分逻辑：
    # change_rate > 0.5 且 momentum > 0 → 接近顶部（80-100）
    # change_rate > 0.2 且 momentum > 0 → 繁荣期（60-80）
    # change_rate > 0 且 momentum > 0 → 爬坡期（40-60）
    # change_rate > 0 但 momentum < 0 → 见顶期（60-80）
    # change_rate < 0 → 下行期（80-100 分，表示更接近顶部已过）

    if change_rate > 0.5:
        if recent_momentum > 0:
            score = min(100, 70 + change_rate * 30)
        else:
            score = min(100, 65 + change_rate * 20)
    elif change_rate > 0.2:
        if recent_momentum > 0:
            score = 55 + change_rate * 20
        else:
            score = 60 + change_rate * 15
    elif change_rate > 0:
        if recent_momentum > 0:
            score = 35 + change_rate * 40
        else:
            score = 50 + change_rate * 30
    elif change_rate > -0.2:
        score = 60 + abs(change_rate) * 50
    else:
        score = min(100, 70 + abs(change_rate) * 30)

    return round(min(100, max(0, score)), 1)

def calc_cap_adjusted_payout(data):
    """
    跨周期平滑分红评估
    用近10年平均利润替代单年利润，计算周期调整后分红率
    """
    profit_history = data.get("profit_history", [])
    dividend_history = data.get("dividend_history", [])

    if not profit_history or not dividend_history:
        return {
            "cap_adjusted_payout_ratio": None,
            "current_payout_ratio": None,
            "dividend_resilience": None,
            "note": "历史数据不足，无法计算周期调整后分红率"
        }

    # 近10年平均利润
    avg_profit_10y = sum(profit_history) / len(profit_history) if profit_history else 0

    # 近3年平均分红
    recent_dividends = dividend_history[-3:] if len(dividend_history) >= 3 else dividend_history
    avg_dividend_3y = sum(recent_dividends) / len(recent_dividends) if recent_dividends else 0

    # 当前年分红
    current_dividend = dividend_history[-1] if dividend_history else 0
    current_profit = profit_history[-1] if profit_history else 0

    # 周期调整后分红率
    cap_ratio = (avg_dividend_3y / avg_profit_10y * 100) if avg_profit_10y > 0 else None

    # 当前分红率（单年）
    current_ratio = (current_dividend / current_profit * 100) if current_profit > 0 else None

    # 分红韧性：低谷期是否仍维持分红
    dividend_resilience = calc_dividend_resilience(profit_history, dividend_history)

    return {
        "cap_adjusted_payout_ratio": round(cap_ratio, 1) if cap_ratio is not None else None,
        "current_payout_ratio": round(current_ratio, 1) if current_ratio is not None else None,
        "avg_profit_10y": round(avg_profit_10y, 2),
        "avg_dividend_3y": round(avg_dividend_3y, 2),
        "dividend_resilience": dividend_resilience
    }

def calc_dividend_resilience(profit_history, dividend_history):
    """
    分红韧性评分：在利润低谷期是否仍维持分红
    返回 resilient / moderate / fragile
    """
    if len(profit_history) < 5 or len(dividend_history) < 5:
        return {"level": "unknown", "note": "数据不足"}

    min_len = min(len(profit_history), len(dividend_history))
    profits = profit_history[-min_len:]
    dividends = dividend_history[-min_len:]

    avg_profit = sum(profits) / len(profits)
    if avg_profit == 0:
        return {"level": "unknown", "note": "平均利润为零"}

    # 找利润低谷年份
    min_profit_idx = profits.index(min(profits))
    min_profit = profits[min_profit_idx]
    min_dividend = dividends[min_profit_idx]

    # 低谷期分红率
    trough_payout = (min_dividend / min_profit * 100) if min_profit > 0 else None

    # 检查低谷期是否仍分红
    if min_profit <= 0 and min_dividend > 0:
        return {
            "level": "fragile",
            "trough_payout": None,
            "note": f"低谷期利润为负 ({min_profit:.1f})但仍分红 ({min_dividend:.1f})，靠历史积累维持，不可持续"
        }

    if trough_payout is None:
        return {"level": "unknown", "note": "无法计算低谷期分红率"}

    if trough_payout <= 60:
        return {
            "level": "resilient",
            "trough_payout": round(trough_payout, 1),
            "note": f"低谷期分红率 {trough_payout:.1f}%，分红韧性极强，低谷期仍可持续"
        }
    elif trough_payout <= 100:
        return {
            "level": "moderate",
            "trough_payout": round(trough_payout, 1),
            "note": f"低谷期分红率 {trough_payout:.1f}%，分红韧性一般，低谷期分红占利润比例偏高"
        }
    else:
        return {
            "level": "fragile",
            "trough_payout": round(trough_payout, 1),
            "note": f"低谷期分红率 {trough_payout:.1f}% > 100%，利润不足以支撑分红，靠消耗历史积累"
        }

def detect_capex_cycle_risk(data):
    """
    CAPEX周期错配检测
    港口扩产周期5-8年，CAPEX高峰往往对应行业景气高点
    扩产高峰后3-5年产能释放，可能供过于求
    """
    capex_dep_ratio = data.get("capex_depreciation_ratio", 1.0)
    cycle_position = data.get("_cycle_position", "expansion")

    risk_level = "low"
    risk_detail = ""

    if capex_dep_ratio > 2.5:
        if cycle_position in ["peak", "downturn"]:
            risk_level = "high"
            risk_detail = f"CAPEX/折旧比 {capex_dep_ratio:.1f} 处于高位且行业已见顶/下行，扩产产能释放后可能面临供过于求"
        elif cycle_position in ["expansion"]:
            risk_level = "medium"
            risk_detail = f"CAPEX/折旧比 {capex_dep_ratio:.1f} 较高，行业处于繁荣期，需关注扩产周期与行业周期的匹配度"
        else:
            risk_level = "low"
            risk_detail = f"CAPEX/折旧比 {capex_dep_ratio:.1f} 较高，但行业处于低谷/爬坡期，逆周期扩产可能有利"
    elif capex_dep_ratio > 1.5:
        risk_level = "low"
        risk_detail = f"CAPEX/折旧比 {capex_dep_ratio:.1f} 适中，资本开支处于正常水平"
    else:
        risk_detail = f"CAPEX/折旧比 {capex_dep_ratio:.1f} 较低，资本开支收缩，可能反映行业信心不足"

    return {
        "capex_depreciation_ratio": round(capex_dep_ratio, 2),
        "risk_level": risk_level,
        "detail": risk_detail
    }

def analyze_throughput_structure(data):
    """
    吞吐量结构韧性分析
    评估业务结构对周期波动的抵御能力
    """
    structure = data.get("throughput_structure", {})

    domestic_ratio = structure.get("domestic_ratio", 0.5)
    container_ratio = structure.get("container_ratio", 0.5)
    top3_partner_concentration = structure.get("top3_partner_concentration", 0.5)

    # 韧性评分（越高越抗周期）
    # 内贸占比高 → 更抗周期
    domestic_score = domestic_ratio * 40  # 0-40 分
    # 集装箱占比高 → 受消费影响但相对稳定；散货受原材料波动影响更大
    container_score = container_ratio * 30  # 0-30 分
    # 贸易伙伴集中度低 → 更分散风险
    diversification_score = (1 - top3_partner_concentration) * 30  # 0-30 分

    total_resilience = domestic_score + container_score + diversification_score

    if total_resilience >= 60:
        resilience_level = "high"
        resilience_desc = "业务结构对周期波动抵御能力强"
    elif total_resilience >= 40:
        resilience_level = "moderate"
        resilience_desc = "业务结构对周期波动抵御能力一般"
    else:
        resilience_level = "low"
        resilience_desc = "业务结构对周期波动抵御能力弱，外贸/散货占比高，集中度高"

    return {
        "resilience_score": round(total_resilience, 1),
        "resilience_level": resilience_level,
        "resilience_desc": resilience_desc,
        "domestic_ratio": domestic_ratio,
        "container_ratio": container_ratio,
        "top3_partner_concentration": top3_partner_concentration,
        "risk_factors": identify_structure_risks(structure)
    }

def identify_structure_risks(structure):
    """识别吞吐量结构中的风险因素"""
    risks = []

    domestic_ratio = structure.get("domestic_ratio", 0.5)
    if domestic_ratio < 0.3:
        risks.append("外贸占比超过70%，对全球贸易波动高度敏感")

    container_ratio = structure.get("container_ratio", 0.5)
    if container_ratio < 0.3:
        risks.append("散货占比超过70%，受原材料价格和需求波动影响大")

    top3_concentration = structure.get("top3_partner_concentration", 0.5)
    if top3_concentration > 0.6:
        risks.append(f"前三大贸易伙伴集中度 {top3_concentration:.0%}，地缘政治风险敞口大")

    return risks if risks else ["无显著结构性风险"]

def main():
    parser = argparse.ArgumentParser(description="周期股专项检测引擎")
    parser.add_argument("--data", required=True, help="JSON 格式的周期性检测数据")
    args = parser.parse_args()

    try:
        data = json.loads(args.data)

        # 1. 周期位置定位
        cycle_result = detect_cycle_position(data)

        # 2. 跨周期平滑分红评估
        cap_result = calc_cap_adjusted_payout(data)

        # 3. CAPEX周期错配检测（需要周期位置信息）
        data["_cycle_position"] = cycle_result["position"]
        capex_result = detect_capex_cycle_risk(data)

        # 4. 吞吐量结构韧性分析
        structure_result = analyze_throughput_structure(data)

        output = {
            "status": "success",
            "industry": data.get("industry", "port"),
            "cycle_position": cycle_result,
            "cap_adjusted_dividend": cap_result,
            "capex_cycle_risk": capex_result,
            "throughput_structure_resilience": structure_result,
            "investment_implication": generate_implication(cycle_result, cap_result, capex_result, structure_result)
        }

        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(0)

    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"JSON 解析失败: {str(e)}"}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"计算失败: {str(e)}"}), file=sys.stderr)
        sys.exit(1)

def generate_implication(cycle, cap, capex, structure):
    """生成投资建议"""
    position = cycle["position"]
    resilience = cap.get("dividend_resilience", {}).get("level", "unknown")
    capex_risk = capex["risk_level"]

    implications = []

    # 基于周期位置的建议
    if position == "trough":
        implications.append("周期低谷期：红利投资者可逆向布局分红韧性强的标的，等待周期回升")
    elif position == "recovery":
        implications.append("周期爬坡期：最佳红利配置窗口，利润回升将提升分红可持续性")
    elif position == "expansion":
        implications.append("周期繁荣期：分红安全，但需开始关注先行指标拐点信号")
    elif position == "peak":
        implications.append("周期见顶期：需防御为主，高分红可能是'夕阳红包'")
    elif position == "downturn":
        implications.append("周期下行期：谨慎观望，等待先行指标企稳信号")

    # 基于分红韧性的建议
    if resilience == "resilient":
        implications.append("分红韧性强：低谷期仍可持续分红，适合长期持有")
    elif resilience == "fragile":
        implications.append("分红韧性弱：周期下行时分红可能削减，不宜重仓")

    # 基于 CAPEX 风险的建议
    if capex_risk == "high":
        implications.append("CAPEX周期错配风险高：扩产产能释放后可能面临供过于求，挤压利润和分红")
    elif capex_risk == "medium":
        implications.append("CAPEX周期风险需关注：扩产规模较大，需评估项目回报率")

    # 基于业务结构的建议
    struct_level = structure.get("resilience_level", "moderate")
    if struct_level == "low":
        implications.append("业务结构抗周期能力弱：外贸/散货占比高，下行周期冲击更大")

    return implications

if __name__ == "__main__":
    main()
