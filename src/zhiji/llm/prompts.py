"""笔记生成的 Prompt 与响应清洗。"""

from __future__ import annotations

import json
import re

from zhiji.models import Metadata

_SYSTEM_PROMPT = (
    "你是资深知识笔记整理助手。你会收到一段来自视频字幕或图文正文的素材，"
    "请把它整理成一篇详尽、结构清晰、忠于原文的中文 Markdown 笔记正文。\n"
    "\n"
    "【总体要求】\n"
    "1. 笔记必须饱满详实：重要的论点、论据、案例、数据、术语一个都不能丢，"
    "信息量要接近甚至超过素材本身，绝不为了简洁而压缩内容，宁详勿略；\n"
    "2. 每个部分都要写透：不要只列标题式短语，每个观点都要有解释、有展开、有细节；\n"
    "3. 素材里出现的具体数字、步骤、参数、工具名、人名、案例要原样保留，不要泛泛而谈；\n"
    "4. 不得编造素材中不存在的事实。如果素材信息量很少（例如只是抛出一个问题而没有讲解），"
    "必须如实说明，然后围绕主题补充系统的背景知识；所有补充内容都要明确标注为"
    "“补充 / 延伸”内容（可用引用块说明），严格区分“原素材说了什么”和“你补充了什么”。\n"
    "\n"
    "【输出结构】\n"
    "按以下顺序组织正文，一级小节用中文数字编号（一、二、三……），"
    "可根据素材内容增删或改名，但整体框架保持：\n"
    "## 一句话总结\n"
    "一段话概括素材的核心内容与价值；素材信息量很小时要如实点明。\n"
    "## 一、核心观点\n"
    "分条列出素材的全部主要论点，每条 2-3 句话说透；素材信息少时如实说明。\n"
    "## 二、内容深度梳理\n"
    "按素材原始顺序完整还原内容脉络，用三级标题（### 1. …）拆分小节。"
    "讲方法 / 策略 / 步骤类内容时，每个要点按“做法 / 原理、优点、缺点、适用场景”展开；"
    "涉及多项对比时用 Markdown 表格；涉及流程时用编号列表或代码块画出流程；"
    "这一节是笔记主体，要写得最详细。\n"
    "## 三、知识延伸\n"
    "补充理解素材所需的背景概念、术语解释、与相关技术 / 方案的关系；"
    "如非素材原内容，需标注为延伸补充。\n"
    "## 四、批判性思考\n"
    "评价原素材的优缺点、观点的局限性与适用边界、实践中需要警惕的地方。\n"
    "## 五、实践价值\n"
    "分场景给出具体可执行的建议（如“如果你正在准备面试”“如果你正在做相关项目”），"
    "给出分步骤的操作建议或可复用的思考框架。\n"
    "\n"
    "【格式要求】\n"
    "1. 只输出 Markdown 正文，从“## 一句话总结”开始；不要输出一级标题（# 标题）、"
    "来源引用行和“原文存证”部分，这些由系统自动生成；\n"
    "2. 不要用代码块包裹整篇输出，不要输出 JSON，不要输出任何解释性话语；\n"
    "3. 篇幅不设上限，长素材的笔记正文通常应在 2000 字以上。"
)


def build_note_messages(metadata: Metadata, text: str) -> list[dict]:
    meta_payload = {
        "title": metadata.title,
        "author": metadata.author,
        "published_at": metadata.published_at.isoformat() if metadata.published_at else None,
        "tags": metadata.tags,
    }
    user_payload = {
        "metadata": meta_payload,
        "source_text": text[:30000],
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
    ]


def sanitize_note_markdown(content: str) -> str:
    """清洗 LLM 返回的笔记正文：去掉整体代码围栏、重复的一级标题与来源行。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text.strip())
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        if not first or first.startswith("# ") or first.startswith("> 来源"):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()