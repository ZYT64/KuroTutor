"""领域工具包注册。

把 :mod:`kurotutor.tools` 下各业务工具统一注册进主注册表。
工具 handler 遵循约定：``async def handler(ctx: ToolContext, **params) -> str``。

这里是「领域工具」的单一装配点；:func:`build_default_registry` 供 CLI / serve 使用，
返回一个填满核心业务工具、可直接注入 :class:`~kurotutor.agent.core.Agent` 的注册表。
"""

from __future__ import annotations

from typing import Any

from kurotutor.agent.registry import ToolRegistry

from . import (
    bank,
    basic,
    classroom,
    code,
    diagnostic,
    docs,
    goal,
    grade_homework,
    image_split,
    kb,
    kb_corpus,
    lecture_gen,
    notebook,
    ocr_tools,
    quiz,
    report,
    review,
    school,
    solve_photo,
    web,
    wrongbook,
)


def _p(desc: str, _type: str = "string") -> dict[str, Any]:
    """构造一个 property 定义。"""
    return {"type": _type, "description": desc}


def build_default_registry() -> ToolRegistry:
    """构造并返回一个注册了全部核心业务工具的工具注册表。"""
    registry = ToolRegistry()

    # 基础：当前时间
    registry.register(
        "now",
        "获取当前本地时间与星期，用于排课/提醒/复习调度判断。",
        {"type": "object", "properties": {}},
        basic.now,
        category="basic",
    )

    # 错题本
    registry.register(
        "wrongbook_add",
        "把一道错题记入学生的错题本。解题/批改发现学生做错，或学生要求记错题时使用。",
        {
            "type": "object",
            "properties": {
                "question": _p("题目内容", "string"),
                "knowledge_point": _p("知识点，格式『学科/章节/名称』，如『数学/函数/二次函数』"),
                "subject": _p("学科，如数学、物理"),
                "student_answer": _p("学生作答"),
                "correct_answer": _p("正确答案"),
                "analysis": _p("讲解/方法，供复习时引用"),
                "error_type": _p("错误类型：概念不清/计算错误/审题失误/步骤缺失/其他"),
                "image_path": _p("题目图片在工作区的路径"),
                "source": _p("来源：photo/text/homework"),
            },
            "required": ["question"],
        },
        wrongbook.add_wrong_question,
        category="wrongbook",
        sandbox_required=True,
    )

    registry.register(
        "wrongbook_query",
        "查询学生的错题本记录，可按学科/状态过滤。用于复习安排、学情查看。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科过滤"),
                "status": _p("状态过滤：to_review/reviewing/mastered/archived"),
                "limit": {"type": "integer", "description": "最多返回条数"},
            },
        },
        wrongbook.query_wrong_questions,
        category="wrongbook",
    )

    # 笔记本
    registry.register(
        "notebook_add",
        "把一条笔记存入学生的笔记本（如解析后的笔记图内容）。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科"),
                "topic": _p("主题，如『函数』"),
                "summary": _p("摘要"),
                "content": _p("笔记正文"),
                "notebook": _p("笔记本名，缺省按学科自动归类"),
                "source": _p("来源：image/text/import"),
                "source_path": _p("来源文件路径"),
            },
            "required": ["summary"],
        },
        notebook.add_notebook,
        category="notebook",
        sandbox_required=True,
    )

    registry.register(
        "notebook_query",
        "查询学生的笔记本，可按笔记本名/关键词检索。",
        {
            "type": "object",
            "properties": {
                "notebook": _p("笔记本名过滤"),
                "keyword": _p("关键词"),
                "limit": {"type": "integer", "description": "最多返回条数"},
            },
        },
        notebook.query_notebook,
        category="notebook",
    )

    # 知识库方法卡片
    registry.register(
        "kb_deposit",
        "沉淀一张方法卡片到知识库（题型→方法→步骤→易错点）。每次解题后提炼方法时使用，越用越强。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科"),
                "question_type": _p("题型，如『二次函数求根』"),
                "method": _p("方法总述"),
                "steps": _p("解题步骤，换行分隔"),
                "pitfalls": _p("易错点，换行分隔"),
                "source": _p("从哪次解题提炼"),
            },
            "required": ["subject", "question_type", "method"],
        },
        kb.deposit_card,
        category="kb",
    )

    registry.register(
        "kb_search",
        "检索知识库方法卡片。用于在讲解/出题时引用已有方法。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科过滤"),
                "query": _p("题型/方法关键词"),
                "limit": {"type": "integer", "description": "最多返回条数"},
            },
        },
        kb.search_cards,
        category="kb",
    )

    registry.register(
        "corpus_add",
        "知识库语料库：入库一段教学资料（讲义/教材/百科），含学科/标题/来源/标签。用于积累可检索的大段资料。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科"),
                "title": _p("标题"),
                "content": _p("语料正文"),
                "source": _p("来源：lecture/textbook/web/import"),
                "tags": _p("标签，逗号分隔"),
            },
            "required": ["content"],
        },
        kb_corpus.corpus_add,
        category="kb",
    )

    registry.register(
        "corpus_search",
        "检索知识库语料库（大段资料）。用于讲解时引用教材/讲义内容。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科过滤"),
                "query": _p("关键词"),
                "limit": {"type": "integer", "description": "最多返回条数"},
            },
        },
        kb_corpus.corpus_search,
        category="kb",
    )

    # 复习引擎
    registry.register(
        "quiz_generate",
        "个性化出题：按主题/知识点/画像薄弱点生成题目（可基于错题出变式）。学生要求出题、练题，"
        "或你讲完错题想出同考点变式时使用。参数：topic、knowledge_point、count、difficulty、purpose、variants。",
        {
            "type": "object",
            "properties": {
                "topic": _p("主题，如『二次函数』"),
                "knowledge_point": _p("知识点，格式『学科/章节/名称』"),
                "count": {"type": "integer", "description": "题数（1-10，默认 3）"},
                "difficulty": _p("easy/medium/hard，默认 medium"),
                "purpose": _p("巩固练习/变式训练/考前冲刺"),
                "variants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "变式母题题干列表（如从刚讲过的错题出变式）",
                },
            },
        },
        quiz.quiz_generate,
        category="quiz",
    )

    registry.register(
        "quiz_check",
        "判分学生作答——仅针对最近 quiz_generate 出的练习题"
        "（入学诊断要用 diagnostic_submit，别用这个）。答错自动记错题本并排复习。"
        "参数：answers（学生答案，多题用 | 分隔）、question_index（只判第几题，可选）。",
        {
            "type": "object",
            "properties": {
                "answers": _p("学生的答案；多题用 | 分隔，按出题顺序"),
                "question_index": {"type": "integer", "description": "只判第几题（1 起），可选"},
            },
            "required": ["answers"],
        },
        quiz.quiz_check,
        category="quiz",
    )

    # 定时课堂
    registry.register(
        "schedule_class",
        "排课：单堂课或系列课（系列课会按学生目标自动设计大纲、每周同一时间）。"
        "学生说『帮我约一节课』『我要上xx课』『排个系列课』时使用。自然语言时间请先换算为 ISO。"
        "参数：subject、topic、start_at、minutes、series_count、goal。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科，如 数学/物理"),
                "topic": _p("课题，如『二次函数图像与性质』"),
                "start_at": _p("上课时间（本地 ISO，如 2026-08-30T15:00:00）"),
                "minutes": {"type": "integer", "description": "时长分钟，默认 45"},
                "series_count": {"type": "integer", "description": "系列课节数（单堂课不传）"},
                "goal": _p("系列课目标（如『期末上 110 分』）"),
            },
            "required": ["subject", "topic", "start_at"],
        },
        classroom.schedule_class,
        category="classroom",
    )

    registry.register(
        "course_list",
        "查看学生的课程安排（最近 20 节，含状态）。学生问『我有什么课/课表』时使用。",
        {"type": "object", "properties": {}},
        classroom.course_list,
        category="classroom",
    )

    registry.register(
        "reschedule_class",
        "应急改期：把某节课移到新时间（相关备课/开课/下课任务自动重排）。参数：course_id、new_start。",
        {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "课实例编号"},
                "new_start": _p("新时间（本地 ISO）"),
            },
            "required": ["course_id", "new_start"],
        },
        classroom.reschedule_class,
        category="classroom",
    )

    registry.register(
        "cancel_class",
        "应急取消某节课。学生说『取消那节课』时使用。参数：course_id。",
        {
            "type": "object",
            "properties": {"course_id": {"type": "integer", "description": "课实例编号"}},
            "required": ["course_id"],
        },
        classroom.cancel_class,
        category="classroom",
    )

    registry.register(
        "prepare_class",
        "手动触发备课（到点会自动备；学生想提前看讲义时手动触发）。参数：course_id。",
        {
            "type": "object",
            "properties": {"course_id": {"type": "integer", "description": "课实例编号"}},
            "required": ["course_id"],
        },
        classroom.prepare_class,
        category="classroom",
    )

    # 入学诊断
    registry.register(
        "diagnostic_start",
        "入学诊断：给新学生出 3-6 道由易到难的摸底题（按学段学科）。新学生加入、或学生说『测测我的水平』"
        "时使用；也可在学生水平未知、需要建立画像基线时主动提议。参数：subject、count。",
        {
            "type": "object",
            "properties": {
                "subject": _p("学科，默认 数学"),
                "count": {"type": "integer", "description": "题数 3-6，默认 4"},
            },
        },
        diagnostic.diagnostic_start,
        category="diagnostic",
    )

    registry.register(
        "diagnostic_submit",
        "提交诊断答案：逐题判分 → 画像基线写入 → 诊断报告。学生提交答案后立即使用。参数：answers（| 分隔）。",
        {
            "type": "object",
            "properties": {"answers": _p("学生答案，多题用 | 分隔，按出题顺序")},
            "required": ["answers"],
        },
        diagnostic.diagnostic_submit,
        category="diagnostic",
    )

    # 目标管理 + 打卡激励
    registry.register(
        "goal_set",
        "登记学习目标（目标管理）。学生说出目标（如『期末上 110 分』『学会二次函数』）时登记追踪。"
        "参数：goal、subject、target_date、progress。",
        {
            "type": "object",
            "properties": {
                "goal": _p("目标描述"),
                "subject": _p("学科"),
                "target_date": _p("目标日期"),
                "progress": _p("当前进度"),
            },
            "required": ["goal"],
        },
        goal.goal_set,
        category="goal",
    )

    registry.register(
        "goal_list",
        "查看学习目标与进度。学生问『我的目标』时使用。",
        {"type": "object", "properties": {}},
        goal.goal_list,
        category="goal",
    )

    registry.register(
        "goal_update",
        "更新目标进度或状态（达成/放弃）。学生汇报进度或目标达成时使用。参数：goal_id、progress、status。",
        {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "目标编号"},
                "progress": _p("当前进度备注"),
                "status": _p("active/done/dropped"),
            },
            "required": ["goal_id"],
        },
        goal.goal_update,
        category="goal",
    )

    registry.register(
        "daily_checkin",
        "每日学习打卡：连续天数统计 + 里程碑鼓励。学生说『打卡』『签个到』时使用。"
        "参数：note（今日一句话，可选）。",
        {
            "type": "object",
            "properties": {"note": _p("今日学习一句话")},
        },
        goal.daily_checkin,
        category="goal",
    )

    # 代码沙箱
    registry.register(
        "code_run",
        "在隔离子进程执行 Python 代码（仅数学/统计白名单模块，无网络无文件，10 秒超时）。"
        "用于验证计算结果、枚举找规律、检查解题数值——讲理科题时推荐用它核对自己口算的结果。"
        "参数：code、timeout。",
        {
            "type": "object",
            "properties": {
                "code": _p("Python 代码（print 输出结果）"),
                "timeout": {"type": "integer", "description": "超时秒数，默认 10"},
            },
            "required": ["code"],
        },
        code.code_run,
        category="code",
    )

    # 学习周报
    registry.register(
        "weekly_report",
        "生成本周学习周报（错题统计/掌握变化/复习完成度，LLM 润色 + Word 文档导出）。"
        "学生要看周报/学情总结时使用。",
        {"type": "object", "properties": {}},
        report.weekly_report,
        category="report",
    )

    registry.register(
        "report_subscribe",
        "订阅/退订每周日晚 8 点自动学习周报推送。参数：op（subscribe/unsubscribe）。",
        {
            "type": "object",
            "properties": {"op": _p("subscribe / unsubscribe，默认 subscribe")},
        },
        report.report_subscribe,
        category="report",
    )

    # 校本同步
    registry.register(
        "school_sync",
        "校本同步：登记/查看学校教材版本、当前章节、考试安排。"
        "学生提到学校进度/教材/考试时登记；出题与备课优先贴合校本进度。参数：op（set/get）、textbook、chapter、exam_date、note。",
        {
            "type": "object",
            "properties": {
                "op": _p("set（登记）/ get（查看），默认 get"),
                "textbook": _p("教材版本，如 人教版"),
                "chapter": _p("当前学校进度章节，如 二次函数"),
                "exam_date": _p("下次考试日期或描述"),
                "note": _p("备注（老师进度等）"),
            },
        },
        school.school_sync,
        category="school",
    )

    registry.register(
        "plot_function",
        "画函数图像（坐标网格 + 多条曲线，PNG）。讲函数/方程需要看图时使用。"
        "参数：expressions（如 x^2-2*x-3，多个用列表或逗号）、x_min、x_max、title。",
        {
            "type": "object",
            "properties": {
                "expressions": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string", "description": "逗号分隔的多个表达式"},
                    ],
                    "description": "函数表达式，变量 x；支持 +-*/^ 与 sin/cos/tan/sqrt/abs/log/ln/exp、pi/e",
                },
                "x_min": {"type": "number", "description": "x 范围下界，默认 -10"},
                "x_max": {"type": "number", "description": "x 范围上界，默认 10"},
                "title": _p("图标题，可选"),
            },
            "required": ["expressions"],
        },
        quiz.plot_function,
        category="quiz",
        sandbox_required=True,
    )

    registry.register(
        "grade_homework",
        "作业批改：读取作业图片，逐题判分、归类错因、错题自动记入错题本并更新画像。学生发来作业图时使用。",
        {
            "type": "object",
            "properties": {
                "path": _p("作业图片在工作区的本地路径"),
            },
            "required": ["path"],
        },
        grade_homework.grade_homework,
        category="grading",
        sandbox_required=True,
    )

    registry.register(
        "lecture_gen",
        "讲义生成：把某个主题生成结构化、可打印的 Markdown 讲义文件（长内容模式①）。"
        "用于系统讲解、定时课堂备课、或学生要求详细讲义时。",
        {
            "type": "object",
            "properties": {
                "topic": _p("讲义主题，如『二次函数』"),
                "subject": _p("学科（可选）"),
            },
            "required": ["topic"],
        },
        lecture_gen.lecture_gen,
        category="lecture",
        sandbox_required=True,
    )

    registry.register(
        "web_fetch",
        "抓取一个网页并返回正文（去标签、截断）。用于查资料。参数：url。",
        {
            "type": "object",
            "properties": {"url": _p("网页链接"), "limit": {"type": "integer", "description": "截断字数"}},
            "required": ["url"],
        },
        web.web_fetch,
        category="web",
    )

    registry.register(
        "web_search",
        "网络搜索。默认 Bing（免密钥、国内可达）；可选 Tavily（结果带正文摘要，需 models.search.api_key）；"
        "所选供应商失败自动兜底。结果已含标题/链接/摘要，一般直接引用即可，无需再 web_fetch 原文。"
        "参数：query、limit、provider（bing/tavily/duckduckgo，可选）。",
        {
            "type": "object",
            "properties": {
                "query": _p("搜索关键词"),
                "limit": {"type": "integer", "description": "返回条数"},
                "provider": _p("bing（默认）/ tavily / duckduckgo，可选，缺省用配置"),
            },
            "required": ["query"],
        },
        web.web_search,
        category="web",
    )

    registry.register(
        "review_due",
        "列出学生到期待复习的错题（间隔重复到期）。用于安排复习、主动推送复习。",
        {"type": "object", "properties": {}},
        review.review_due,
        category="review",
    )

    registry.register(
        "review_answer",
        "记录一次复习/复测结果。参数 wq_id（错题编号），mastered（bool：是否答对）；"
        "掌握则标记 mastered，未掌握则继续强化。",
        {
            "type": "object",
            "properties": {
                "wq_id": {"type": "integer", "description": "错题编号"},
                "mastered": {"type": "boolean", "description": "本次复习是否答对"},
            },
            "required": ["wq_id", "mastered"],
        },
        review.review_answer,
        category="review",
    )

    registry.register(
        "review_schedule",
        "为某道错题排下一次复习任务（按间隔重复计算时间）。参数 wq_id。",
        {
            "type": "object",
            "properties": {
                "wq_id": {"type": "integer", "description": "错题编号"},
                "delay_seconds": {"type": "integer", "description": "距下次复习秒数（缺省按公式）"},
            },
            "required": ["wq_id"],
        },
        review.review_schedule,
        category="review",
    )

    # 视觉：拍照解题 / 通用看图理解
    registry.register(
        "solve_photo",
        "拍照解题：读取题目图片，返回结构化「题目/答案/方法卡片」，并自动完成画像更新、"
        "方法卡沉淀、错题记录或询问。学生发来题目照片时使用；讲解打磨由你（Agent）依据教学法完成。",
        {
            "type": "object",
            "properties": {
                "path": _p("题目图片在工作区的本地路径"),
                "image_path": _p("题目图片路径（path 的别名）"),
                "student_answer": _p("学生的作答，用于判分与错题策略（可选）"),
            },
            "required": ["path"],
        },
        solve_photo.solve_photo,
        category="vision",
        sandbox_required=True,
    )

    registry.register(
        "notebook_photo",
        "发笔记图 → 视觉解析内容 → 智能归类 → 自动存入笔记本。"
        "学生发来笔记图片时使用（如课堂笔记、错题整理的照片）。",
        {
            "type": "object",
            "properties": {
                "path": _p("笔记图片在工作区的本地路径"),
            },
            "required": ["path"],
        },
        notebook.parse_photo,
        category="notebook",
        sandbox_required=True,
    )

    registry.register(
        "image_understand",
        "通用看图理解：让视觉模型按自定义 prompt 描述一张图片。用于解析笔记图、批改作业等场景。",
        {
            "type": "object",
            "properties": {
                "path": _p("图片在工作区的本地路径"),
                "prompt": _p("对该图片的提问/描述要求"),
            },
            "required": ["path"],
        },
        solve_photo.image_understand,
        category="vision",
        sandbox_required=True,
    )

    # 自动切题（仅题集录入用；讲题走 solve_photo 整图视觉，不切图）
    registry.register(
        "split_photo",
        "题集录入工具：把整页多题图片切成逐题图（存 qbench/），用于把一套题录入题库——"
        "学生/家长明确要求『录入这套题 / 建题集 / 存进题库』时才调用。"
        "注意：讲题不要用它，学生问题直接用 solve_photo 整图讲解。"
        "自动处理跨页：本页开头的上一页残句会与上一次录入的尾块比对，连续则自动缝合为完整题。参数：path。",
        {"type": "object", "properties": {"path": _p("图片路径")}, "required": ["path"]},
        image_split.split_photo,
        category="vision",
        sandbox_required=True,
    )

    # 题集文档切题（PDF / Word / PPT），仅录入用
    registry.register(
        "split_document",
        "题集录入工具（文档版）：把整份试卷文档（.pdf / .docx / .doc / .pptx / .ppt）逐页切分成题图，"
        "自动缝合跨页题。用于批量录入题库（要求录入整套试卷/习题册时调用）。讲题不要用它。参数：path。",
        {
            "type": "object",
            "properties": {"path": _p("文档路径（.pdf/.docx/.doc/.pptx/.ppt）")},
            "required": ["path"],
        },
        image_split.split_document,
        category="vision",
        sandbox_required=True,
    )

    # 跨页题缝合
    registry.register(
        "merge_crops",
        "把多张题目裁片按顺序垂直拼接为一张完整题图（跨页题缝合、学生分多次拍同一题时用）。参数：paths（图片路径列表，按上下顺序）、out（可选输出路径）。",
        {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "图片路径列表，按上下顺序",
                },
                "out": _p("输出路径，可选"),
            },
            "required": ["paths"],
        },
        image_split.merge_crops,
        category="vision",
        sandbox_required=True,
    )

    # 题集（错题 + 好题收藏）
    registry.register(
        "bank_add",
        "把一道题录入学生题集（收藏：错题 error / 好题 good）。何时调用见系统提示词【题集录入策略】——"
        "学生完全不懂的错题、你认为非常好的题自动录入；拿不准的先问学生；简单粗心错不录。参数：question、kind、subject、knowledge_point、image_path、reason。",
        {
            "type": "object",
            "properties": {
                "question": _p("题目内容（文字形式，图片题可简述题干）"),
                "kind": _p("error=错题 / good=好题，默认 good"),
                "subject": _p("学科，如数学、物理"),
                "knowledge_point": _p("知识点，格式『学科/章节/名称』"),
                "image_path": _p("题图路径（工作区内，可选）"),
                "reason": _p("一句话收录理由，会告诉学生"),
            },
            "required": ["question"],
        },
        bank.bank_add,
        category="bank",
        sandbox_required=True,
    )

    registry.register(
        "bank_list",
        "查看学生题集（错题+好题收藏）。学生问『题集里有什么/收了哪些题』时用。参数：kind（可选）、limit（可选）。",
        {
            "type": "object",
            "properties": {
                "kind": _p("error / good，缺省全部"),
                "limit": _p("最多显示条数，默认 10"),
            },
        },
        bank.bank_list,
        category="bank",
    )

    registry.register(
        "bank_remove",
        "从题集移除一道题。学生说『这道别收藏了/移出题集』时用。参数：question_id。",
        {
            "type": "object",
            "properties": {"question_id": _p("题集编号（bank_list 里查到）")},
            "required": ["question_id"],
        },
        bank.bank_remove,
        category="bank",
        sandbox_required=True,
    )

    registry.register(
        "bank_extract",
        "按条件从题集筛选题目并组卷导出（PDF/Word，format 选 pdf/docx；题图嵌入、中文排版）。"
        "学生说『把错题整理成卷子/提取xx的好题』时用。参数：kind、subject、keyword、limit、format。",
        {
            "type": "object",
            "properties": {
                "kind": _p("error / good，缺省全部"),
                "subject": _p("按学科筛选，可选"),
                "keyword": _p("按题干/知识点关键词筛选，可选"),
                "limit": _p("最多提取题数，默认 50"),
                "format": _p("导出格式：pdf（默认）/ docx"),
            },
        },
        bank.bank_extract,
        category="bank",
        sandbox_required=True,
    )

    # 文档识别（OCR 识别链 + MinerU 复杂版面）
    registry.register(
        "ocr_read",
        "识别图片或扫描 PDF 里的文字。仅在 doc_read 读取后提示『无文本层/扫描件』时才使用本工具"
        "（有文字层的 PDF 直接用 doc_read，更快更准）。自动按配置的识别链尝试"
        "（默认：百度 → 腾讯 → 本地，免费额度优先，引擎自动降级）。"
        "复杂版面（大量公式/表格/双栏）请改用 mineru_parse。参数：path、max_pages。",
        {
            "type": "object",
            "properties": {
                "path": _p("图片或 PDF 的工作区路径"),
                "max_pages": _p("PDF 最大识别页数（默认 10）"),
            },
            "required": ["path"],
        },
        ocr_tools.ocr_read,
        category="docs",
    )

    registry.register(
        "mineru_parse",
        "MinerU 复杂版面解析（专用，勿滥用）：仅当扫描件经 ocr_read 识别后质量太差、"
        "或多模态确认含大量公式/表格/双栏排版时才使用（把这类文件转成 Markdown）。"
        "有文字层的 PDF、普通文字扫描件一律不用本工具（每日免费额度有限，解析耗时较长）。参数：path。",
        {
            "type": "object",
            "properties": {"path": _p("PDF 或图片的工作区路径")},
            "required": ["path"],
        },
        ocr_tools.mineru_parse,
        category="docs",
    )

    # 通用文档能力（Word / PPT / PDF）
    registry.register(
        "doc_read",
        "读取文档内容（.docx/.pptx/.pdf/.txt/.md → 结构化文本）。"
        "学生发来课件/讲义/试卷文档要讲解时先用它读。参数：path。",
        {"type": "object", "properties": {"path": _p("文档路径")}, "required": ["path"]},
        docs.doc_read,
        category="docs",
    )

    registry.register(
        "doc_write",
        "生成文档：把轻量标记内容写成 .docx / .pptx / .pdf。"
        "标记：`# ` 大标题、`## ` 节标题（pptx 中为新一页幻灯片）、`- ` 列表项、普通行为段落。"
        "参数：path、content。",
        {
            "type": "object",
            "properties": {
                "path": _p("输出路径，后缀决定格式（.docx/.pptx/.pdf）"),
                "content": _p("轻量标记内容"),
            },
            "required": ["path", "content"],
        },
        docs.doc_write,
        category="docs",
        sandbox_required=True,
    )

    registry.register(
        "doc_edit",
        "编辑既有文档：op=append（把轻量标记内容追加到 docx/pptx 末尾）；"
        "op=replace（把 docx 中原文 content 替换为 replacement）。参数：path、op、content、replacement。",
        {
            "type": "object",
            "properties": {
                "path": _p("文档路径"),
                "op": _p("append / replace"),
                "content": _p("追加的内容，或要被替换的原文"),
                "replacement": _p("替换后的新文本（replace 时必填）"),
            },
            "required": ["path", "op", "content"],
        },
        docs.doc_edit,
        category="docs",
        sandbox_required=True,
    )

    registry.register(
        "pdf_ops",
        "PDF 页级操作：op=merge（按顺序合并多个 PDF）；op=extract（抽页另存，pages 格式如 1,3,5-8）。"
        "参数：op、paths（merge）/ src+pages（extract）、path（输出）。",
        {
            "type": "object",
            "properties": {
                "op": _p("merge / extract"),
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "merge：要合并的 PDF 路径列表",
                },
                "src": _p("extract：源 PDF 路径"),
                "pages": _p("extract：页码，如 1,3,5-8"),
                "path": _p("输出 PDF 路径"),
            },
            "required": ["op", "path"],
        },
        docs.pdf_ops,
        category="docs",
        sandbox_required=True,
    )

    registry.register(
        "doc_convert",
        "文档格式互转（经 LibreOffice，如 pdf→docx、docx→pdf、pptx→pdf）。参数：path、target_fmt。",
        {
            "type": "object",
            "properties": {"path": _p("源文档路径"), "target_fmt": _p("目标格式：pdf/docx/pptx/txt 等")},
            "required": ["path", "target_fmt"],
        },
        docs.doc_convert,
        category="docs",
        sandbox_required=True,
    )

    return registry
