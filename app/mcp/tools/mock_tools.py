"""
MCP 工具 Mock 实现 — P0 阶段使用本地模拟，P2 阶段替换为真实 MCP 调用。

Usage:
    from app.mcp.tools.mock_tools import estimate_freight, estimate_route
    freight = estimate_freight(volume=18.5, has_pet=True, from_addr="朝阳", to_addr="海淀")
"""

from __future__ import annotations

import math
from typing import Any

# ── 车型价格表 ────────────────────────────────────────────

VEHICLE_PRICE_TABLE: dict[str, dict[str, Any]] = {
    "small_van": {
        "type": "小面包车",
        "capacity_m3": 8,
        "max_load_kg": 800,
        "base_price_yuan": 150,
        "per_km_yuan": 5,
        "pet_friendly": False,
    },
    "4.2m_truck": {
        "type": "4.2m 厢式货车",
        "capacity_m3": 20,
        "max_load_kg": 2000,
        "base_price_yuan": 300,
        "per_km_yuan": 8,
        "pet_friendly": False,
    },
    "4.2m_truck_pet": {
        "type": "4.2m 宠物友好厢式货车",
        "capacity_m3": 20,
        "max_load_kg": 2000,
        "base_price_yuan": 350,
        "per_km_yuan": 8,
        "pet_friendly": True,
    },
    "6.8m_truck": {
        "type": "6.8m 厢式货车",
        "capacity_m3": 32,
        "max_load_kg": 5000,
        "base_price_yuan": 500,
        "per_km_yuan": 12,
        "pet_friendly": False,
    },
}

# ── 城市对距离表 ──────────────────────────────────────────

CITY_DISTANCE_MATRIX: dict[tuple[str, str], dict[str, Any]] = {
    ("朝阳", "海淀"): {"distance_km": 18.5, "duration_min": 45, "toll_yuan": 15},
    ("朝阳", "通州"): {"distance_km": 25.0, "duration_min": 55, "toll_yuan": 20},
    ("朝阳", "大兴"): {"distance_km": 35.0, "duration_min": 65, "toll_yuan": 25},
    ("朝阳", "丰台"): {"distance_km": 22.0, "duration_min": 50, "toll_yuan": 15},
    ("海淀", "朝阳"): {"distance_km": 18.5, "duration_min": 45, "toll_yuan": 15},
    ("海淀", "通州"): {"distance_km": 40.0, "duration_min": 75, "toll_yuan": 30},
    ("海淀", "大兴"): {"distance_km": 32.0, "duration_min": 60, "toll_yuan": 20},
    ("浦东", "徐汇"): {"distance_km": 12.0, "duration_min": 30, "toll_yuan": 0},
    ("浦东", "静安"): {"distance_km": 15.0, "duration_min": 35, "toll_yuan": 0},
}

# ── 典型物品体积参考 ──────────────────────────────────────

ITEM_VOLUME_REF: dict[str, dict[str, Any]] = {
    "双人床": {"volume_m3": 1.2, "category": "卧室家具", "fragile": False, "special_handling": None},
    "单人床": {"volume_m3": 0.7, "category": "卧室家具", "fragile": False, "special_handling": None},
    "床垫": {"volume_m3": 0.8, "category": "卧室家具", "fragile": False, "special_handling": "需防潮膜包裹"},
    "衣柜": {"volume_m3": 1.5, "category": "卧室家具", "fragile": False, "special_handling": "需拆卸或直立搬运"},
    "沙发": {"volume_m3": 2.0, "category": "客厅家具", "fragile": False, "special_handling": "需软毯包裹"},
    "茶几": {"volume_m3": 0.3, "category": "客厅家具", "fragile": True, "special_handling": "玻璃面需单独包装"},
    "电视柜": {"volume_m3": 0.6, "category": "客厅家具", "fragile": False, "special_handling": None},
    "餐桌": {"volume_m3": 0.8, "category": "餐厅家具", "fragile": False, "special_handling": "可拆卸桌腿"},
    "餐椅": {"volume_m3": 0.15, "category": "餐厅家具", "fragile": False, "special_handling": None},
    "冰箱": {"volume_m3": 0.9, "category": "厨房家电", "fragile": True, "special_handling": "直立运输，需提前除霜"},
    "洗衣机": {"volume_m3": 0.5, "category": "厨房家电", "fragile": True, "special_handling": "需拆运输螺栓"},
    "电视机": {"volume_m3": 0.2, "category": "电子产品", "fragile": True, "special_handling": "原厂包装或气泡膜+纸箱"},
    "书桌": {"volume_m3": 0.5, "category": "书房家具", "fragile": False, "special_handling": None},
    "书架": {"volume_m3": 0.8, "category": "书房家具", "fragile": False, "special_handling": "清空书籍后搬运"},
    "三角钢琴": {"volume_m3": 3.5, "category": "大件乐器", "fragile": True, "special_handling": "专业钢琴搬运+可能需吊装"},
    "立式钢琴": {"volume_m3": 2.0, "category": "大件乐器", "fragile": True, "special_handling": "专业钢琴搬运"},
}


def match_vehicle(volume_m3: float, has_pet: bool = False) -> dict[str, Any]:
    """根据体积和宠物需求匹配车型。"""
    # 从最小车型开始匹配
    candidates = ["small_van", "4.2m_truck", "6.8m_truck"]
    for vt in candidates:
        if volume_m3 <= VEHICLE_PRICE_TABLE[vt]["capacity_m3"]:
            if has_pet and vt == "4.2m_truck":
                return VEHICLE_PRICE_TABLE["4.2m_truck_pet"]
            return VEHICLE_PRICE_TABLE[vt]
    # 超大体积 → 最大车型
    return VEHICLE_PRICE_TABLE["6.8m_truck"]


def estimate_distance(from_addr: str, to_addr: str) -> dict[str, Any]:
    """估算城市对距离（从距离矩阵查，否则估算）。"""
    # 提取区级关键词
    from_short = _extract_district(from_addr)
    to_short = _extract_district(to_addr)

    # 查表
    key = (from_short, to_short)
    if key in CITY_DISTANCE_MATRIX:
        return CITY_DISTANCE_MATRIX[key]

    # 反向查
    rev_key = (to_short, from_short)
    if rev_key in CITY_DISTANCE_MATRIX:
        return CITY_DISTANCE_MATRIX[rev_key]

    # 同城默认
    if from_short[:2] == to_short[:2]:
        return {"distance_km": 20.0, "duration_min": 50, "toll_yuan": 10}

    # 跨城默认
    return {"distance_km": 150.0, "duration_min": 180, "toll_yuan": 80}


def _extract_district(addr: str | None) -> str:
    """从地址中提取区名。"""
    if not addr:
        return "朝阳"  # 默认
    for district in ["朝阳", "海淀", "通州", "大兴", "丰台", "东城", "西城",
                      "浦东", "徐汇", "静安", "黄浦", "长宁", "闵行"]:
        if district in addr:
            return district
    return addr[:2] if len(addr) >= 2 else addr


def estimate_freight(
    from_addr: str,
    to_addr: str,
    total_volume_m3: float,
    has_pet: bool = False,
    has_large_items: bool = False,
    large_item_count: int = 0,
    has_elevator_origin: bool = True,
    has_elevator_dest: bool = True,
    floor_origin: int = 1,
    floor_dest: int = 1,
) -> dict[str, Any]:
    """
    货拉拉运费模拟（Mock）。

    Args:
        from_addr: 搬出地址
        to_addr: 搬入地址
        total_volume_m3: 物品总体积
        has_pet: 是否有宠物
        has_large_items: 是否有大件
        large_item_count: 大件数量
        has_elevator_origin: 搬出地有电梯
        has_elevator_dest: 搬入地有电梯
        floor_origin: 搬出楼层
        floor_dest: 搬入楼层

    Returns:
        {vehicle, price_breakdown, notes}
    """
    # 匹配车型
    vehicle = match_vehicle(total_volume_m3, has_pet=has_pet)

    # 估算距离
    route = estimate_distance(from_addr, to_addr)
    distance_km = route["distance_km"]

    # 计算费用
    base = vehicle["base_price_yuan"]
    per_km = vehicle["per_km_yuan"]
    distance_fee = max(0, distance_km - 5) * per_km  # 前 5km 含在起步价

    surcharges = 0
    surcharge_items: list[dict[str, Any]] = []

    if has_pet:
        pet_fee = 50
        surcharges += pet_fee
        surcharge_items.append({"name": "宠物服务费", "amount": pet_fee})

    if has_large_items and large_item_count > 0:
        large_fee = 200 * large_item_count
        surcharges += large_fee
        surcharge_items.append({"name": f"大件附加费 (×{large_item_count})", "amount": large_fee})

    if not has_elevator_origin:
        stair_fee = floor_origin * 30
        surcharges += stair_fee
        surcharge_items.append({"name": f"搬出楼梯费 ({floor_origin}层)", "amount": stair_fee})

    if not has_elevator_dest:
        stair_fee = floor_dest * 30
        surcharges += stair_fee
        surcharge_items.append({"name": f"搬入楼梯费 ({floor_dest}层)", "amount": stair_fee})

    total = base + distance_fee + surcharges
    uncertainty_pct = 0.15

    capacity_ok = total_volume_m3 <= vehicle["capacity_m3"]

    return {
        "recommended_vehicle": {
            "type": vehicle["type"],
            "capacity_m3": vehicle["capacity_m3"],
            "max_load_kg": vehicle["max_load_kg"],
            "pet_friendly": vehicle["pet_friendly"],
            "capacity_fit": capacity_ok,
        },
        "price_breakdown": {
            "base_price_yuan": base,
            "per_km_yuan": per_km,
            "distance_km": distance_km,
            "distance_fee_yuan": round(distance_fee, 2),
            "surcharges": surcharge_items,
            "total_surcharges_yuan": surcharges,
            "total_estimate_yuan": round(total, 2),
            "price_range": {
                "min_yuan": round(total * (1 - uncertainty_pct)),
                "max_yuan": round(total * (1 + uncertainty_pct)),
            },
            "currency": "CNY",
        },
        "route": {
            "distance_km": distance_km,
            "duration_min": route["duration_min"],
            "toll_yuan": route["toll_yuan"],
        },
        "notes": [
            "此为本地模拟报价，实际价格以货拉拉 APP 为准",
            f"容量: {total_volume_m3:.1f}m³ / {vehicle['capacity_m3']}m³ {'✅ 合适' if capacity_ok else '⚠️ 不足'}",
        ],
        "uncertainty_pct": round(uncertainty_pct * 100),
        "uncertainty_level": "medium",  # 模块6: Mock 数据属于中等置信度
        "source": "mock_simulation",
    }


def estimate_route(
    from_addr: str,
    to_addr: str,
    vehicle_length_m: float = 4.2,
) -> dict[str, Any]:
    """
    高德路线规划模拟（Mock）。

    Args:
        from_addr: 起点地址
        to_addr: 终点地址
        vehicle_length_m: 车长（m）

    Returns:
        {routes, recommended_route_index}
    """
    route = estimate_distance(from_addr, to_addr)

    return {
        "routes": [
            {
                "route_id": "route_001",
                "distance_km": route["distance_km"],
                "duration_min": route["duration_min"],
                "toll_yuan": route["toll_yuan"],
                "traffic_condition": "moderate",
                "restrictions": [
                    "北四环货车限行 07:00-09:00, 17:00-20:00",
                ] if vehicle_length_m >= 4.2 else [],
                "alternative_start_time": "10:00",
            }
        ],
        "recommended_route_index": 0,
        "source": "mock_simulation",
    }


# ── 物品清单生成规则 ─────────────────────────────────────

ROOM_INVENTORY_MAP: dict[str, list[dict[str, Any]]] = {
    "主卧": [
        {"name": "双人床", "category": "卧室家具", "qty": 1},
        {"name": "床垫", "category": "卧室家具", "qty": 1},
        {"name": "床头柜", "category": "卧室家具", "qty": 2},
        {"name": "衣柜", "category": "卧室家具", "qty": 1},
        {"name": "梳妆台", "category": "卧室家具", "qty": 1},
    ],
    "次卧": [
        {"name": "单人床", "category": "卧室家具", "qty": 1},
        {"name": "床垫", "category": "卧室家具", "qty": 1},
        {"name": "书桌", "category": "书房家具", "qty": 1},
        {"name": "衣柜", "category": "卧室家具", "qty": 1},
    ],
    "客厅": [
        {"name": "沙发", "category": "客厅家具", "qty": 1},
        {"name": "茶几", "category": "客厅家具", "qty": 1},
        {"name": "电视柜", "category": "客厅家具", "qty": 1},
        {"name": "电视机", "category": "电子产品", "qty": 1},
    ],
    "餐厅": [
        {"name": "餐桌", "category": "餐厅家具", "qty": 1},
        {"name": "餐椅", "category": "餐厅家具", "qty": 4},
    ],
    "厨房": [
        {"name": "冰箱", "category": "厨房家电", "qty": 1},
        {"name": "洗衣机", "category": "厨房家电", "qty": 1},
    ],
    "书房": [
        {"name": "书桌", "category": "书房家具", "qty": 1},
        {"name": "书架", "category": "书房家具", "qty": 2},
    ],
}

LAYOUT_ROOMS: dict[str, list[str]] = {
    "开间": ["主卧", "客厅", "厨房"],
    "一居室": ["主卧", "客厅", "厨房", "餐厅"],
    "两居室": ["主卧", "次卧", "客厅", "厨房", "餐厅", "书房"],
    "三居室": ["主卧", "次卧", "次卧", "客厅", "厨房", "餐厅", "书房"],
    "四居室": ["主卧", "次卧", "次卧", "次卧", "客厅", "厨房", "餐厅", "书房"],
    "复式": ["主卧", "次卧", "次卧", "客厅", "厨房", "餐厅", "书房", "客厅"],
    "别墅": ["主卧", "次卧", "次卧", "次卧", "客厅", "厨房", "餐厅", "书房", "客厅"],
}


def generate_inventory(
    layout: str = "三居室",
    has_pet: bool = False,
    has_large_items_list: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    基于户型 + 特殊物品生成物品清单。

    Args:
        layout: 户型（如 "三居室"）
        has_pet: 是否有宠物
        has_large_items_list: 用户声明的大件物品列表

    Returns:
        物品清单 [{item_id, name, category, quantity, volume_m3, fragile, special_handling}]
    """
    rooms = LAYOUT_ROOMS.get(layout, LAYOUT_ROOMS["三居室"])
    items: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    item_counter = 0

    for room in rooms:
        room_items = ROOM_INVENTORY_MAP.get(room, [])
        for ri in room_items:
            name = ri["name"]
            # 同名字的物品合并（如多个次卧的衣柜）
            if name in seen_names:
                for existing in items:
                    if existing["name"] == name:
                        existing["quantity"] += ri["qty"]
                        break
            else:
                seen_names.add(name)
                vol_ref = ITEM_VOLUME_REF.get(name, {"volume_m3": 0.3, "fragile": False, "special_handling": None})
                item_counter += 1
                items.append({
                    "item_id": f"inv_{item_counter:04d}",
                    "name": name,
                    "category": ri["category"],
                    "quantity": ri["qty"],
                    "estimated_volume_m3": vol_ref["volume_m3"],
                    "fragile": vol_ref.get("fragile", False),
                    "special_handling": vol_ref.get("special_handling"),
                    "room_origin": room,
                })

    # 添加宠物相关物品
    if has_pet:
        item_counter += 1
        items.append({
            "item_id": f"inv_{item_counter:04d}",
            "name": "宠物用品箱",
            "category": "宠物用品",
            "quantity": 1,
            "estimated_volume_m3": 0.3,
            "fragile": False,
            "special_handling": "猫砂盆需密封、宠物食品需密封防潮",
            "room_origin": "全屋",
        })

    # 添加用户声明的大件物品
    if has_large_items_list:
        for large in has_large_items_list:
            name = large.get("name", "")
            vol_ref = ITEM_VOLUME_REF.get(name, {"volume_m3": 2.0, "fragile": True, "special_handling": "大件特殊搬运"})
            item_counter += 1
            items.append({
                "item_id": f"inv_{item_counter:04d}",
                "name": name,
                "category": large.get("category", "大件物品"),
                "quantity": large.get("quantity", 1),
                "estimated_volume_m3": large.get("estimated_volume_m3", vol_ref["volume_m3"]),
                "fragile": large.get("fragile", vol_ref.get("fragile", True)),
                "special_handling": large.get("special_handling", vol_ref.get("special_handling")),
                "room_origin": large.get("room_origin", "用户指定"),
            })

    return items


def calculate_box_plan(total_volume_m3: float) -> dict[str, int]:
    """根据总体积估算所需箱型及数量。"""
    small_vol_share = 0.15   # 小件占总体积的比例
    medium_vol_share = 0.35
    large_vol_share = 0.40
    wardrobe_vol_share = 0.10

    small_box_vol = 0.036   # 40×30×30cm = 36L
    medium_box_vol = 0.080  # 50×40×40cm = 80L
    large_box_vol = 0.150   # 60×50×50cm = 150L
    wardrobe_box_count = max(2, math.ceil(total_volume_m3 * wardrobe_vol_share / 0.200))

    return {
        "small_boxes": max(5, math.ceil(total_volume_m3 * small_vol_share / small_box_vol)),
        "medium_boxes": max(3, math.ceil(total_volume_m3 * medium_vol_share / medium_box_vol)),
        "large_boxes": max(1, math.ceil(total_volume_m3 * large_vol_share / large_box_vol)),
        "wardrobe_boxes": wardrobe_box_count,
    }
