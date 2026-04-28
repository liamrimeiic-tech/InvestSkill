#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
烟蒂股资产垫计算引擎（专家级版本 v3）
计算T0/T1/T2净资产价值、资产烧损率、资产垫分级
支持动态存货系数、其他应收款、兑现路径量化指标
支持合同负债T0剔除、FVTOCI/CDs现金池穿透
"""

import argparse
import json
import sys


def get_inventory_coefficient(inventory_type):
    """
    获取存货动态系数（专家级颗粒度）
    
    参数:
        inventory_type: 存货类型
    
    返回:
        存货折扣系数
    """
    inventory_coeff_map = {
        "hard_currency": 0.85,      # 白酒/贵金属
        "general_manufacturing": 0.65,  # 一般制造/房地产
        "electronics_fashion": 0.4,     # 电子产品/时尚服装
        "high_risk": 0.15,             # 高风险库存
    }
    return inventory_coeff_map.get(inventory_type, 0.65)  # 默认一般制造


def validate_inputs(data):
    """验证输入数据格式"""
    required_fields = ['cash_pool', 'total_debt', 'interest_debt', 'total_shares', 'ar', 'inventory']
    
    for field in required_fields:
        if field not in data:
            raise ValueError(f"缺少必填字段: {field}")
        if not isinstance(data[field], (int, float)):
            raise ValueError(f"字段 {field} 必须为数字")
    
    if data['total_shares'] <= 0:
        raise ValueError("总股本必须大于0")
    
    if data['cash_pool'] < 0 or data['total_debt'] < 0:
        raise ValueError("现金池和总负债不能为负数")


def calculate_nav(data):
    """计算资产垫核心指标（专家级版本v3）"""
    cash_pool = data['cash_pool']
    total_debt = data['total_debt']
    interest_debt = data['interest_debt']
    total_shares = data['total_shares']
    ar = data['ar']
    other_ar = data.get('other_ar', 0)  # 其他应收款
    inventory = data['inventory']
    inventory_type = data.get('inventory_type', 'general_manufacturing')  # 存货类型
    price = data.get('price')
    fcf = data.get('fcf')
    
    # 新增：现金池深水区穿透
    fvtoci_assets = data.get('fvtoci_assets', 0)  # FVTOCI中高流动性债券
    debt_investment_cds = data.get('debt_investment_cds', 0)  # 债权投资中的大额存单/CDs
    
    # 新增：合同负债（T0可剔除）
    contract_liabilities = data.get('contract_liabilities', 0)  # 合同负债/预收款项
    
    # 新增：兑现路径量化指标
    annual_dividend = data.get('annual_dividend', 0)  # 年度派息
    parent_market_cap = data.get('parent_market_cap', 0)  # 母公司市值
    subsidiary_holdings_value = data.get('subsidiary_holdings_value', 0)  # 所持子公司市值
    
    # 有效现金池 = 现金池 + FVTOCI + CDs
    effective_cash = cash_pool + fvtoci_assets + debt_investment_cds
    
    # T0: 净现金法（合同负债可从总负债剔除）
    t0_nav = (effective_cash - (total_debt - contract_liabilities)) / total_shares
    
    # T1: 有息负债扣除法（有息负债不包含合同负债，无需剔除）
    t1_nav = (effective_cash - interest_debt) / total_shares
    
    # T2: 清算价值法（动态系数版，保守使用完整总负债，FVTOCI系数0.9）
    inventory_coeff = get_inventory_coefficient(inventory_type)
    t2_nav = (cash_pool * 1.0 + 
              fvtoci_assets * 0.9 +   # FVTOCI清算时微折
              debt_investment_cds * 1.0 +  # CDs本质现金
              ar * 0.85 + 
              other_ar * 0.2 +  # 其他应收款系数0.2
              inventory * inventory_coeff - 
              total_debt) / total_shares  # T2使用完整总负债（保守）
    
    # 资产垫(取T0/T1/T2中的最大正值)
    asset_cushion_per_share = max(t0_nav, t1_nav, t2_nav, 0)
    asset_cushion = asset_cushion_per_share * total_shares
    
    # 资产烧损率（优化版：max(0, -FCF)）
    burn_rate = None
    veto_flag = False
    burn_rate_level = None
    
    if fcf is not None and asset_cushion > 0:
        if fcf < 0:  # 负FCF表示烧钱
            burn_rate = abs(fcf) / asset_cushion
            
            # 优化阈值：>=20%触发否决
            if burn_rate >= 0.20:
                veto_flag = True
                burn_rate_level = "否决"
            elif burn_rate >= 0.05:
                burn_rate_level = "预警"
            else:
                burn_rate_level = "安全"
        else:  # 正FCF不烧钱
            burn_rate = 0
            burn_rate_level = "安全"
    
    # 资产垫分级
    grade = "未通过"
    premium_t0 = None
    premium_t1 = None
    premium_t2 = None
    
    if price is not None and price > 0:
        # T0级: 股价低于T0的70%(安全边际30%)
        if t0_nav > 0 and price <= t0_nav * 0.7:
            grade = "T0"
            premium_t0 = ((t0_nav - price) / price) * 100
        # T1级: 股价低于T1的80%
        elif t1_nav > 0 and price <= t1_nav * 0.8:
            grade = "T1"
            premium_t1 = ((t1_nav - price) / price) * 100
        # T2级: 股价低于T2的90%
        elif t2_nav > 0 and price <= t2_nav * 0.9:
            grade = "T2"
            premium_t2 = ((t2_nav - price) / price) * 100
    
    # 兑现路径量化指标计算
    # A型：现金池/派息比率
    dividend_ratio = None
    if annual_dividend > 0:
        dividend_ratio = cash_pool / annual_dividend
    
    # B型：持股覆盖率
    holding_coverage = None
    if parent_market_cap > 0:
        holding_coverage = (subsidiary_holdings_value / parent_market_cap) * 100
    
    return {
        "T0_NAV": round(t0_nav, 4),
        "T1_NAV": round(t1_nav, 4),
        "T2_NAV": round(t2_nav, 4),
        "asset_cushion": round(asset_cushion, 2),
        "burn_rate": round(burn_rate, 4) if burn_rate is not None else None,
        "burn_rate_level": burn_rate_level,
        "pricing_grade": grade,
        "veto_flag": veto_flag,
        "premium_t0": round(premium_t0, 2) if premium_t0 is not None else None,
        "premium_t1": round(premium_t1, 2) if premium_t1 is not None else None,
        "premium_t2": round(premium_t2, 2) if premium_t2 is not None else None,
        
        # 兑现路径量化指标
        "dividend_ratio": round(dividend_ratio, 2) if dividend_ratio is not None else None,
        "dividend_ratio_pass": dividend_ratio >= 1.5 if dividend_ratio is not None else None,
        "holding_coverage": round(holding_coverage, 2) if holding_coverage is not None else None,
        "holding_coverage_pass": holding_coverage >= 50 if holding_coverage is not None else None,
        
        # 存货系数信息
        "inventory_coeff": inventory_coeff,
        "inventory_type": inventory_type,
        
        # 新增：现金池穿透明细
        "effective_cash_pool": round(effective_cash, 2),
        "fvtoci_assets": fvtoci_assets,
        "debt_investment_cds": debt_investment_cds,
        "contract_liabilities": contract_liabilities,
    }


def main():
    parser = argparse.ArgumentParser(
        description="烟蒂股资产垫计算引擎（专家级版本v3）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础案例
  python cigar_calculator.py --data '{"cash_pool":100,"total_debt":30,"interest_debt":20,"total_shares":10,"ar":15,"inventory":20,"price":5.0}'
  
  # 包含现金池穿透与合同负债
  python cigar_calculator.py --data '{"cash_pool":100,"total_debt":30,"interest_debt":20,"total_shares":10,"ar":15,"other_ar":5,"inventory":20,"inventory_type":"general_manufacturing","price":5.0,"fvtoci_assets":8,"debt_investment_cds":5,"contract_liabilities":5}'
  
  # 包含兑现路径指标
  python cigar_calculator.py --data '{"cash_pool":50,"total_debt":10,"interest_debt":0,"total_shares":5,"ar":10,"inventory":5,"price":6.0,"fcf":8,"annual_dividend":3,"parent_market_cap":300,"subsidiary_holdings_value":200}'

说明:
  - 必填参数: cash_pool(现金池), total_debt(总负债), interest_debt(有息负债), total_shares(总股本), ar(应收账款), inventory(存货)
  - 可选参数: 
    * price(当前股价)
    * fcf(自由现金流)
    * other_ar(其他应收款，系数0.2)
    * inventory_type(存货类型: hard_currency/general_manufacturing/electronics_fashion/high_risk)
    * fvtoci_assets(FVTOCI中高流动性债券，计入T0/T1现金池，T2系数0.9)
    * debt_investment_cds(债权投资中的大额存单/CDs，全额计入现金池)
    * contract_liabilities(合同负债/预收款项，T0计算中从总负债剔除)
    * annual_dividend(年度派息总额)
    * parent_market_cap(母公司总市值)
    * subsidiary_holdings_value(所持子公司股权市值)
  
  - 单位: 金额单位统一为亿元或万元，股本单位为亿股或万股，需保持一致
  
  - 公式说明:
    * T0 = (现金池 + FVTOCI + CDs - (总负债 - 合同负债)) / 总股本
    * T1 = (现金池 + FVTOCI + CDs - 有息负债) / 总股本
    * T2 = (现金池*1.0 + FVTOCI*0.9 + CDs*1.0 + AR*0.85 + 其他AR*0.2 + 存货*动态系数 - 总负债) / 总股本
    * 资产烧损率阈值>=20%触发否决
        """
    )
    
    parser.add_argument("--data", required=True, help="JSON格式输入数据")
    
    args = parser.parse_args()
    
    try:
        # 解析JSON输入
        data = json.loads(args.data)
        
        # 验证输入
        validate_inputs(data)
        
        # 计算指标
        result = calculate_nav(data)
        
        # 添加状态
        result["status"] = "success"
        
        # 输出JSON结果
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
    except json.JSONDecodeError as e:
        error_result = {
            "status": "error",
            "message": f"JSON解析失败: {str(e)}"
        }
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)
    except ValueError as e:
        error_result = {
            "status": "error",
            "message": str(e)
        }
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        error_result = {
            "status": "error",
            "message": f"计算失败: {str(e)}"
        }
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
