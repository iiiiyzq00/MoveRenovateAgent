"""
ChromaDB 连接管理 — 向量知识库客户端。

支持:
- 远程 ChromaDB 服务器 (HTTP)
- 本地持久化目录 (PersistentClient)

Usage:
    from app.rag.chroma_client import get_chroma_client
    client = get_chroma_client()
    collection = client.get_or_create_collection("moving_renovation_kb")
"""

from __future__ import annotations

import logging
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.types import EmbeddingFunction

from app.config import get_config

logger = logging.getLogger(__name__)

# 全局单例
_chroma_client: ClientAPI | None = None


def get_chroma_client() -> ClientAPI:
    """
    获取 ChromaDB 客户端单例。

    优先 HTTP 远程连接，失败则降级到本地持久化。
    """
    global _chroma_client

    if _chroma_client is not None:
        return _chroma_client

    cfg = get_config()

    # 尝试 HTTP 连接
    try:
        _chroma_client = chromadb.HttpClient(
            host=cfg.chromadb_host,
            port=cfg.chromadb_port,
        )
        # 验证连接
        _chroma_client.heartbeat()
        logger.info(f"ChromaDB HTTP client connected: {cfg.chromadb_host}:{cfg.chromadb_port}")
    except Exception as e:
        logger.warning(f"ChromaDB HTTP connection failed ({e}), falling back to local persistence")
        import os
        os.makedirs(cfg.chromadb_persist_dir, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=cfg.chromadb_persist_dir,
        )
        logger.info(f"ChromaDB Persistent client at: {cfg.chromadb_persist_dir}")

    return _chroma_client


def reset_client() -> None:
    """重置客户端（测试用）。"""
    global _chroma_client
    _chroma_client = None


class KnowledgeBaseManager:
    """
    搬装知识库管理器 — 封装 Collection CRUD 和种子数据加载。

    按第二阶段 5.1 决策，知识库冷启动策略：
    - 50 条行业规范（authority_level=mandatory_standard）
    - 150 条 LLM 生成知识（authority_level=llm_generated）
    """

    SEED_DOCUMENTS: list[dict[str, Any]] = [
        # ── 物品打包 (item_packing) ──
        {
            "content": "冰箱搬运前需提前24小时断电除霜，内部物品清空，门用胶带固定但不要锁死（留有缝隙防止密封条变形）。运输时冰箱必须直立放置，倾斜角度不得超过45度，否则压缩机润滑油会流入制冷管路导致损坏。到新家后需静置2-4小时再通电。",
            "metadata": {
                "source": "standard",
                "authority_level": "mandatory_standard",
                "category": "item_packing",
                "subcategory": "家电搬运",
                "tags": ["冰箱", "除霜", "直立运输", "静置", "家电"],
                "applicable_items": ["冰箱", "冰柜", "冷藏设备"],
                "difficulty_level": "intermediate",
                "feedback_score": 0.90,
                "feedback_count": 10,
            },
        },
        {
            "content": "洗衣机搬运前需拆除运输螺栓（防止滚筒晃动损坏），排空内部残水，用原厂包装或气泡膜包裹机身。搬运时保持直立，不可倒置。到新家后需重新安装运输螺栓检查、调平底脚。",
            "metadata": {
                "source": "standard",
                "authority_level": "mandatory_standard",
                "category": "item_packing",
                "subcategory": "家电搬运",
                "tags": ["洗衣机", "运输螺栓", "排水", "家电"],
                "applicable_items": ["洗衣机", "烘干机"],
                "difficulty_level": "intermediate",
                "feedback_score": 0.85,
                "feedback_count": 8,
            },
        },
        {
            "content": "钢琴搬运需使用专业钢琴搬运带和防震垫，搬运前锁紧键盘盖并用软毯包裹琴体。三角钢琴需拆卸支腿并水平搬运，立式钢琴可直立但需固定防止晃动。上楼梯或窄通道时需评估是否需要吊装服务。建议使用4个以上专业搬运人员。",
            "metadata": {
                "source": "standard",
                "authority_level": "industry_best_practice",
                "category": "item_packing",
                "subcategory": "大件搬运",
                "tags": ["钢琴", "大件", "吊装", "专业搬运"],
                "applicable_items": ["三角钢琴", "立式钢琴"],
                "difficulty_level": "advanced",
                "feedback_score": 0.92,
                "feedback_count": 15,
            },
        },
        {
            "content": "衣物和被褥可用真空压缩袋压缩后装入纸箱，节省约60%空间。套装和易皱衣物建议用挂衣箱搬运（wardrobe box），直接挂入不折叠。鞋子用鞋盒或软纸包裹后放入箱底。",
            "metadata": {
                "source": "llm_generated",
                "authority_level": "llm_generated",
                "category": "item_packing",
                "subcategory": "衣物打包",
                "tags": ["衣物", "压缩袋", "挂衣箱", "软物品"],
                "applicable_items": ["衣物", "被褥", "鞋子"],
                "difficulty_level": "basic",
                "feedback_score": 0.75,
                "feedback_count": 5,
            },
        },
        # ── 施工规范 (construction_standard) ──
        {
            "content": "卫生间防水施工规范：地面防水层应从地面延伸到墙面，淋浴区墙面防水高度不低于1800mm，洗手盆区不低于1200mm，其他区域不低于300mm。防水层厚度不小于1.5mm。施工完成后需进行24小时闭水试验，水位不低于20mm，无渗漏方为合格。参考标准：GB 50327-2001《住宅装饰装修工程施工规范》§6.3。",
            "metadata": {
                "source": "standard",
                "authority_level": "mandatory_standard",
                "category": "construction_standard",
                "subcategory": "防水工程",
                "tags": ["防水", "卫生间", "闭水试验", "GB50327"],
                "applicable_rooms": ["卫生间", "厨房", "阳台"],
                "difficulty_level": "intermediate",
                "feedback_score": 0.95,
                "feedback_count": 20,
            },
        },
        # ── 安全规范 (safety_code) ──
        {
            "content": "住宅电路改造安全规范：普通插座回路导线截面不小于2.5mm²铜芯线，空调/电热水器等大功率电器应单独回路且不小于4mm²。配电箱总开关额定电流不大于40A。卫生间和厨房所有插座必须带漏电保护（≤30mA）。强弱电管线间距不小于300mm，交叉处需做屏蔽处理。参考标准：GB 50327-2001 §5.2 及 JGJ 16-2008《民用建筑电气设计规范》。",
            "metadata": {
                "source": "standard",
                "authority_level": "mandatory_standard",
                "category": "safety_code",
                "subcategory": "电路安全",
                "tags": ["电路", "导线截面", "漏电保护", "配电箱", "GB50327"],
                "applicable_rooms": ["全屋"],
                "difficulty_level": "advanced",
                "feedback_score": 0.93,
                "feedback_count": 18,
            },
        },
        {
            "content": "燃气管道改造必须由具有燃气施工资质的专业单位进行，严禁私自改动。燃气管道不得穿过卧室、卫生间，与电气开关水平距离不小于300mm。燃气表不得安装在密闭柜体内，需保证通风。改造完成后须进行气密性试验，出具检测合格报告。参考标准：GB 50028-2006《城镇燃气设计规范》。",
            "metadata": {
                "source": "standard",
                "authority_level": "mandatory_standard",
                "category": "safety_code",
                "subcategory": "燃气安全",
                "tags": ["燃气", "管道", "资质", "气密性", "GB50028"],
                "applicable_rooms": ["厨房"],
                "difficulty_level": "advanced",
                "feedback_score": 0.96,
                "feedback_count": 14,
            },
        },
        # ── 空间尺寸 (space_dimension) ──
        {
            "content": "卧室家具布局最小间距标准：双人床两侧过道宽度不小于600mm（宜800mm），床尾与墙/家具间距不小于600mm。衣柜门开启前方需留不小于800mm的站立空间。床头柜尺寸一般为宽400-600mm×深400-450mm×高450-550mm。主卧床尺寸推荐：二人用≥1500mm×2000mm。",
            "metadata": {
                "source": "standard",
                "authority_level": "industry_best_practice",
                "category": "space_dimension",
                "subcategory": "卧室布局",
                "tags": ["卧室", "床", "过道", "衣柜", "尺寸"],
                "applicable_rooms": ["主卧", "次卧"],
                "difficulty_level": "basic",
                "feedback_score": 0.82,
                "feedback_count": 7,
            },
        },
        # ── 数量估算 (quantity_estimation) ──
        {
            "content": "搬家纸箱数量估算参考（以90㎡三居室为例）：小号箱(40×30×30cm,~36L)约15-20个（书籍、厨房小物、装饰品），中号箱(50×40×40cm,~80L)约10-15个（衣物、床上用品、玩具），大号箱(60×50×50cm,~150L)约5-8个（被褥、靠垫、厨房电器），挂衣箱约3-5个（套装、大衣）。总计约33-48个纸箱。实际数量根据物品多少上下浮动±20%。",
            "metadata": {
                "source": "llm_generated",
                "authority_level": "llm_generated",
                "category": "quantity_estimation",
                "subcategory": "纸箱估算",
                "tags": ["纸箱", "数量", "三居室", "打包"],
                "applicable_home": ["三居室", "90㎡"],
                "difficulty_level": "basic",
                "feedback_score": 0.78,
                "feedback_count": 12,
            },
        },
        # ── 费用参考 (cost_reference) ──
        {
            "content": "装修费用参考范围（2025年市场价格，90㎡基准）：水电改造约8000-15000元（含材料），防水施工约60-80元/㎡，墙面批荡+刷漆约50-80元/㎡，地面铺砖含水泥砂浆约80-150元/㎡，石膏板吊顶约120-180元/㎡。价格受地区、施工队水平、材料品牌影响浮动±20%。以上为参考价格，实际以当地报价为准。",
            "metadata": {
                "source": "llm_generated",
                "authority_level": "llm_generated",
                "category": "cost_reference",
                "subcategory": "装修单价",
                "tags": ["装修", "价格", "单价", "水电", "防水", "铺砖"],
                "region": "全国",
                "year": 2025,
                "difficulty_level": "basic",
                "feedback_score": 0.72,
                "feedback_count": 9,
            },
        },
    ]

    def __init__(self, embedding_fn: EmbeddingFunction | None = None) -> None:
        self.client = get_chroma_client()
        self.embedding_fn = embedding_fn
        self._collection: Any = None

    @property
    def collection_name(self) -> str:
        cfg = get_config()
        return cfg.chromadb_collection

    def get_or_create_collection(self) -> Any:
        """获取或创建知识库 Collection。"""
        if self._collection is not None:
            return self._collection

        cfg = get_config()
        try:
            self._collection = self.client.get_collection(
                name=cfg.chromadb_collection,
                embedding_function=self.embedding_fn,
            )
            logger.info(f"Collection '{cfg.chromadb_collection}' loaded ({self._collection.count()} docs)")
        except Exception:
            self._collection = self.client.create_collection(
                name=cfg.chromadb_collection,
                embedding_function=self.embedding_fn,
                metadata={"description": "搬装知识库 — 物品打包/施工规范/空间尺寸/费用参考"},
            )
            logger.info(f"Collection '{cfg.chromadb_collection}' created")

        return self._collection

    def seed_if_empty(self) -> int:
        """如果 collection 为空，写入种子数据。返回写入条数。"""
        collection = self.get_or_create_collection()

        if collection.count() > 0:
            logger.info(f"Collection already has {collection.count()} documents, skipping seed")
            return 0

        docs = []
        metadatas = []
        ids = []

        for i, doc in enumerate(self.SEED_DOCUMENTS):
            docs.append(doc["content"])
            metadatas.append(doc["metadata"])
            ids.append(f"seed_doc_{i:04d}")

        collection.add(
            documents=docs,
            metadatas=metadatas,
            ids=ids,
        )

        logger.info(f"Seeded {len(docs)} documents into '{self.collection_name}'")
        return len(docs)

    def delete_collection(self) -> None:
        """删除 collection（测试用）。"""
        cfg = get_config()
        try:
            self.client.delete_collection(cfg.chromadb_collection)
            self._collection = None
            logger.info(f"Collection '{cfg.chromadb_collection}' deleted")
        except Exception:
            pass
