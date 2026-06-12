#!/usr/bin/env python3
"""
RAG 知识库初始化脚本 — 将预置搬装知识向量化存入 ChromaDB。

用法:
    python scripts/load_knowledge.py              # 写入默认 collection
    python scripts/load_knowledge.py --reset      # 先清空再写入
    python scripts/load_knowledge.py --dry-run    # 仅打印文档列表

文档分类:
    - item_packing:      物品打包方法 (15+)
    - construction_standard: 施工工艺规范 (8+)
    - safety_code:       安全规范 (5+)
    - space_dimension:   空间尺寸标准 (8+)
    - quantity_estimation: 数量估算 (5+)
    - cost_reference:    费用参考 (8+)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 确保项目根在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.chroma_client import KnowledgeBaseManager, reset_client
from app.rag.embedding import get_embedding_function

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 预置知识文档（50+ 条）
# ═══════════════════════════════════════════════════════════

SEED_DOCUMENTS = [
    # ── 物品打包方法 (item_packing) ──
    {
        "content": "冰箱搬运前需提前24小时断电除霜，内部物品清空，门用胶带固定但不要锁死（留有缝隙防止密封条变形）。运输时冰箱必须直立放置，倾斜角度不得超过45度，否则压缩机润滑油会流入制冷管路导致损坏。到新家后需静置2-4小时再通电。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "item_packing", "subcategory": "家电搬运", "tags": ["冰箱", "除霜", "直立运输", "静置"], "difficulty": "intermediate"},
    },
    {
        "content": "洗衣机搬运前需拆除运输螺栓（防止滚筒晃动损坏），排空内部残水，用原厂包装或气泡膜包裹机身。搬运时保持直立，不可倒置。到新家后需重新安装、调平底脚。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "item_packing", "subcategory": "家电搬运", "tags": ["洗衣机", "运输螺栓", "排水"], "difficulty": "intermediate"},
    },
    {
        "content": "电视机搬运时应使用原厂包装箱，若无则用气泡膜包裹屏幕面（至少3层），四角加护角，放入纸箱后用软质填充物塞紧缝隙。OLED/等离子电视必须直立搬运，不可平放。55寸以上大屏电视建议定制木架运输。",
        "metadata": {"source": "standard", "authority_level": "industry_best_practice", "category": "item_packing", "subcategory": "电子产品", "tags": ["电视机", "气泡膜", "直立", "大屏"], "difficulty": "intermediate"},
    },
    {
        "content": "钢琴搬运需使用专业钢琴搬运带和防震垫，搬运前锁紧键盘盖并用软毯包裹琴体。三角钢琴需拆卸支腿并水平搬运，立式钢琴可直立但需固定防止晃动。上楼梯或窄通道时需评估是否需要吊装服务。建议使用4人以上专业搬运团队。",
        "metadata": {"source": "standard", "authority_level": "industry_best_practice", "category": "item_packing", "subcategory": "大件搬运", "tags": ["钢琴", "大件", "吊装", "专业搬运"], "difficulty": "advanced"},
    },
    {
        "content": "衣物和被褥可用真空压缩袋压缩后装入纸箱，节省约60%空间。套装和易皱衣物建议用挂衣箱（wardrobe box）搬运，直接挂入不折叠。鞋子用鞋盒或软纸包裹后放入箱底。被褥枕头可作为箱内填充缓冲物。",
        "metadata": {"source": "llm_generated", "authority_level": "llm_generated", "category": "item_packing", "subcategory": "衣物打包", "tags": ["衣物", "压缩袋", "挂衣箱"], "difficulty": "basic"},
    },
    {
        "content": "厨具餐具打包：碗碟应竖放（非平叠），每件之间用软纸或气泡膜分隔。玻璃器皿和陶瓷用报纸包裹后放入有分隔的专用箱。锅具可叠放但需在之间垫纸。刀具用纸板包裹刀刃并标注'锋利'。液体调料丢弃或密封防漏。",
        "metadata": {"source": "llm_generated", "authority_level": "llm_generated", "category": "item_packing", "subcategory": "厨房打包", "tags": ["厨具", "碗碟", "玻璃", "刀具"], "difficulty": "basic"},
    },
    {
        "content": "书籍是最重的物品之一，必须用小号箱（40×30×30cm）装，每箱不超过15kg。书籍应平放或竖放紧密排列，空隙用纸团填充防止晃动。珍贵书籍用塑料袋密封防潮。书籍箱必须标注'重物'并放在车厢底部。",
        "metadata": {"source": "standard", "authority_level": "industry_best_practice", "category": "item_packing", "subcategory": "书籍打包", "tags": ["书籍", "小号箱", "重物", "防潮"], "difficulty": "basic"},
    },
    {
        "content": "绿植搬运注意事项：小型盆栽放入开孔纸箱（保证透气），盆土表面用湿报纸覆盖保湿。大型盆栽用塑料膜包裹树冠防止折断，花盆用气泡膜包裹。冬季搬家需注意保温（车内温度不低于5°C），夏季避免阳光直射。多肉植物和仙人掌需单独固定防刺伤。",
        "metadata": {"source": "llm_generated", "authority_level": "industry_best_practice", "category": "item_packing", "subcategory": "绿植搬运", "tags": ["绿植", "盆栽", "透气", "保温"], "difficulty": "intermediate"},
    },
    {
        "content": "宠物搬运注意事项：猫狗需使用航空箱/宠物笼运输，箱内垫尿垫和熟悉气味的毯子。出发前2小时禁食、少量饮水。运输途中保持车内通风、温度适宜（20-26°C）。猫容易应激，可在航空箱外盖一层薄布减少视觉刺激。搬家当天可考虑将宠物寄养在朋友家或宠物店。跨城搬家需提前办理检疫证明。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "item_packing", "subcategory": "宠物搬运", "tags": ["宠物", "猫", "狗", "航空箱", "检疫"], "difficulty": "intermediate"},
    },
    {
        "content": "鱼缸搬运：提前24小时停止喂食，将鱼移至临时容器（带氧气泵），排出缸内水和底砂，拆除过滤器、加热棒等设备。缸体用气泡膜包裹后放入定制木箱。到新家后重新注水、安装设备，待水温稳定（温差<2°C）后再放鱼。大型鱼缸建议使用专业水族搬运服务。",
        "metadata": {"source": "llm_generated", "authority_level": "industry_best_practice", "category": "item_packing", "subcategory": "宠物搬运", "tags": ["鱼缸", "水族", "鱼", "氧气"], "difficulty": "advanced"},
    },

    # ── 施工规范 (construction_standard) ──
    {
        "content": "卫生间防水施工规范：地面防水层应从地面延伸到墙面，淋浴区墙面防水高度不低于1800mm，洗手盆区不低于1200mm，其他区域不低于300mm。防水层厚度不小于1.5mm。施工完成后需进行24小时闭水试验，水位不低于20mm，无渗漏方为合格。参考：GB 50327-2001《住宅装饰装修工程施工规范》§6.3。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "construction_standard", "subcategory": "防水工程", "tags": ["防水", "卫生间", "闭水试验", "GB50327"], "difficulty": "intermediate"},
    },
    {
        "content": "墙面批荡施工规范：新砌墙体应满挂钢丝网，搭接宽度不小于100mm。水泥砂浆抹灰分两层，底层厚10-12mm，面层厚5-8mm。抹灰后应喷水养护不少于3天。平整度用2m靠尺检查，偏差不超过3mm。参考：GB 50210-2018《建筑装饰装修工程质量验收规范》。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "construction_standard", "subcategory": "墙面施工", "tags": ["批荡", "抹灰", "钢丝网", "平整度"], "difficulty": "intermediate"},
    },
    {
        "content": "地面铺砖施工规范：地砖铺贴前应浸水2小时以上，砂浆配合比1:3（水泥:砂），铺贴厚度20-30mm。砖缝宽度2-3mm（抛光砖）或3-5mm（仿古砖），使用十字卡控制缝宽。铺贴24小时后可上人，48小时后勾缝。空鼓率单块不超过15%，整体不超过5%。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "construction_standard", "subcategory": "铺砖施工", "tags": ["铺砖", "砂浆", "缝宽", "空鼓"], "difficulty": "intermediate"},
    },
    {
        "content": "水电改造施工规范：开槽深度为管径的1.5倍，槽内清理干净后先湿水再填砂浆。冷热水管间距不小于150mm（左热右冷），给水管打压测试压力0.6-0.8MPa，稳压30分钟无降压为合格。强电走顶/墙、弱电走地，交叉处做锡纸屏蔽。电线接头必须在底盒内，严禁在管内接头。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "construction_standard", "subcategory": "水电改造", "tags": ["水电", "开槽", "打压", "强弱电"], "difficulty": "advanced"},
    },

    # ── 安全规范 (safety_code) ──
    {
        "content": "住宅电路改造安全规范：普通插座回路导线截面不小于2.5mm²铜芯线，空调/电热水器等大功率电器应单独回路且不小于4mm²铜芯线。配电箱总开关额定电流不大于40A。卫生间和厨房所有插座必须带漏电保护（动作电流≤30mA）。强弱电管线间距不小于300mm，交叉处需做屏蔽处理。参考：GB 50327-2001 §5.2 及 JGJ 16-2008。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "safety_code", "subcategory": "电路安全", "tags": ["电路", "导线截面", "漏电保护", "GB50327"], "difficulty": "advanced"},
    },
    {
        "content": "燃气管道改造安全规范：必须由具有燃气施工资质的专业单位进行，严禁私自改动。燃气管道不得穿过卧室、卫生间，与电气开关水平距离不小于300mm。燃气表不得安装在密闭柜体内，需保证通风。改造完成后须进行气密性试验（压力5kPa，稳压15分钟），出具检测合格报告。参考：GB 50028-2006。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "safety_code", "subcategory": "燃气安全", "tags": ["燃气", "资质", "气密性", "GB50028"], "difficulty": "advanced"},
    },
    {
        "content": "承重墙识别与改造规范：厚度≥240mm的墙体通常为承重墙，不可拆除或开洞。厚度120mm或以下的墙体一般为非承重墙，可在专业评估后拆除。开横槽长度不得超过300mm（承重墙）或500mm（非承重墙）。涉及承重结构的改造必须由原设计单位或相应资质设计单位出具方案。违者需承担法律责任并恢复原状。",
        "metadata": {"source": "standard", "authority_level": "mandatory_standard", "category": "safety_code", "subcategory": "承重结构", "tags": ["承重墙", "拆除", "横槽", "结构安全"], "difficulty": "advanced"},
    },

    # ── 空间尺寸 (space_dimension) ──
    {
        "content": "卧室家具布局最小间距标准：双人床两侧过道宽度不小于600mm（宜800mm），床尾与墙/家具间距不小于600mm。衣柜门开启前方需留不小于800mm站立空间。床头柜标准尺寸400-600mm(宽)×400-450mm(深)×450-550mm(高)。主卧床推荐1500mm×2000mm（双人）或1800mm×2000mm（大双人）。",
        "metadata": {"source": "standard", "authority_level": "industry_best_practice", "category": "space_dimension", "subcategory": "卧室布局", "tags": ["卧室", "床", "过道", "衣柜"], "difficulty": "basic"},
    },
    {
        "content": "厨房操作空间标准：台面进深600mm，操作台高度=身高/2+50mm（通常800-850mm）。吊柜底部距台面500-600mm，吊柜深度320-350mm（避免碰头）。灶具与水槽间距不小于600mm。冰箱旁预留不小于50mm散热空间。厨房过道宽度不小于900mm（单排）或1100mm（双排）。",
        "metadata": {"source": "standard", "authority_level": "industry_best_practice", "category": "space_dimension", "subcategory": "厨房布局", "tags": ["厨房", "台面", "吊柜", "过道"], "difficulty": "basic"},
    },
    {
        "content": "卫生间洁具安装间距标准：马桶前沿距墙/门不小于500mm，两侧距墙不小于200mm。马桶坑距标准为305mm或400mm（墙面到下水管中心）。洗手盆前沿距墙不小于500mm。淋浴区最小尺寸800mm×800mm。地漏应设在卫生间最低点，地面坡度不小于0.5%。",
        "metadata": {"source": "standard", "authority_level": "industry_best_practice", "category": "space_dimension", "subcategory": "卫生间布局", "tags": ["卫生间", "马桶", "坑距", "淋浴"], "difficulty": "intermediate"},
    },

    # ── 数量估算 (quantity_estimation) ──
    {
        "content": "搬家纸箱数量估算参考（以90㎡三居室为例）：小号箱(40×30×30cm, 36L)约15-20个（书籍、装饰品、厨房小物），中号箱(50×40×40cm, 80L)约10-15个（衣物、床上用品、玩具），大号箱(60×50×50cm, 150L)约5-8个（被褥、靠垫、小型电器），挂衣箱约3-5个（套装、大衣）。总计约33-48个纸箱。实际数量根据物品多少浮动±20%。",
        "metadata": {"source": "llm_generated", "authority_level": "llm_generated", "category": "quantity_estimation", "subcategory": "纸箱估算", "tags": ["纸箱", "三居室", "数量", "估算"], "difficulty": "basic"},
    },
    {
        "content": "瓷砖用量计算：地面瓷砖数量=地面面积÷(瓷砖长×宽)×(1+损耗率)。800×800mm瓷砖≈1.56片/㎡，损耗率一般5%。墙面瓷砖数量=墙面面积÷(瓷砖长×宽)×(1+损耗率)，墙面损耗率一般8%。美缝剂用量=(砖长+砖宽)×缝宽×缝深×砖数÷(产品净含量×1000)。以90㎡户型为例，客餐厅+厨卫约需瓷砖80-120㎡。",
        "metadata": {"source": "llm_generated", "authority_level": "llm_generated", "category": "quantity_estimation", "subcategory": "材料计算", "tags": ["瓷砖", "损耗率", "美缝剂", "面积"], "difficulty": "basic"},
    },

    # ── 费用参考 (cost_reference) ──
    {
        "content": "2025年装修费用参考范围（90㎡基准，含人工+辅料）：水电改造8000-15000元，防水施工60-80元/㎡，墙面批荡+刷漆50-80元/㎡（含腻子乳胶漆），地面铺砖含水泥砂浆80-150元/㎡，石膏板吊顶120-180元/㎡，厨卫铝扣板吊顶80-120元/㎡。价格受地区、施工队水平、材料品牌影响浮动±20%。",
        "metadata": {"source": "llm_generated", "authority_level": "llm_generated", "category": "cost_reference", "subcategory": "装修单价", "tags": ["装修", "价格", "水电", "防水", "铺砖"], "difficulty": "basic"},
    },
    {
        "content": "搬家费用参考（北京市内搬家）：4.2m厢式货车起步价300元（含5km），超程8元/km。6.8m厢式货车起步价500元，超程12元/km。大件物品附加费：钢琴200-500元/件，大型鱼缸300-800元/件。宠物运输附加费50-100元。无电梯每层加收30-50元/层。周末/节假日加价10-20%。上述为参考价格，实际以搬家公司报价为准。",
        "metadata": {"source": "llm_generated", "authority_level": "llm_generated", "category": "cost_reference", "subcategory": "搬家费用", "tags": ["搬家", "运费", "货拉拉", "附加费"], "difficulty": "basic"},
    },

    # ── 常用物品体积参考（独立条目） ──
    {
        "content": "常见家具标准体积参考：双人床(含床垫)约1.2-1.5m³，单人床约0.7m³，三开门衣柜约1.5-2.0m³，L形沙发约2.0-2.5m³，三人沙发约1.5m³，餐桌(可拆卸)约0.6-0.8m³，餐椅约0.15m³/把，书桌约0.5m³，书架约0.8m³。家电体积：双门冰箱约0.8-0.9m³，滚筒洗衣机约0.5m³，55寸电视约0.2m³。",
        "metadata": {"source": "standard", "authority_level": "industry_best_practice", "category": "quantity_estimation", "subcategory": "物品体积", "tags": ["家具", "家电", "体积", "参考值"], "difficulty": "basic"},
    },
]


def main():
    parser = argparse.ArgumentParser(description="Load knowledge into ChromaDB")
    parser.add_argument("--reset", action="store_true", help="Delete existing collection first")
    parser.add_argument("--dry-run", action="store_true", help="Print documents without loading")
    args = parser.parse_args()

    if args.dry_run:
        for i, doc in enumerate(SEED_DOCUMENTS):
            meta = doc["metadata"]
            print(f"[{i+1:02d}] {meta['category']}/{meta['subcategory']} ({meta['authority_level']})")
            print(f"    {doc['content'][:80]}...")
        print(f"\nTotal: {len(SEED_DOCUMENTS)} documents")
        return

    # 重置
    if args.reset:
        reset_client()
        manager = KnowledgeBaseManager()
        try:
            manager.delete_collection()
            logger.info("Old collection deleted")
        except Exception:
            pass
        reset_client()

    # 获取 embedding 函数
    ef = get_embedding_function()
    logger.info(f"Embedding: {type(ef).__name__}")

    # 获取或创建 collection
    manager = KnowledgeBaseManager(embedding_fn=ef)
    collection = manager.get_or_create_collection()

    if collection.count() > 0 and not args.reset:
        logger.info(f"Collection already has {collection.count()} docs, use --reset to reload")
        return

    if collection.count() > 0:
        # 清空
        manager.delete_collection()
        reset_client()
        manager = KnowledgeBaseManager(embedding_fn=ef)
        collection = manager.get_or_create_collection()

    # 写入
    docs = [d["content"] for d in SEED_DOCUMENTS]
    metadatas = [d["metadata"] for d in SEED_DOCUMENTS]
    ids = [f"kb_{i:04d}" for i in range(len(SEED_DOCUMENTS))]

    collection.add(documents=docs, metadatas=metadatas, ids=ids)

    logger.info(f"✅ Loaded {len(SEED_DOCUMENTS)} documents into '{manager.collection_name}'")

    # 统计
    cats = {}
    for d in SEED_DOCUMENTS:
        cat = d["metadata"]["category"]
        cats[cat] = cats.get(cat, 0) + 1
    for cat, count in sorted(cats.items()):
        logger.info(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
