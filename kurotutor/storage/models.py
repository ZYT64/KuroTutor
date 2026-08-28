"""数据层数据模型（SQLModel / SQLite，可切 PG）。

设计原则：
- 时间字段统一用 ``UTC datetime``，由引擎在写入时填充 ``created_at``。
- 枚举值用字符串常量（见各 ``Status`` 分组），避免数据库迁移时枚举类型耦合。
- 画像/错题等需要排序的数据，全部面向「按学生查询最近 N 条」的索引，避免全表扫描。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---- 状态常量（字符串枚举） -------------------------------------------------


# 错题本状态
class WrongStatus:
    TO_REVIEW = "to_review"  # 待复习
    REVIEWING = "reviewing"  # 复习中
    MASTERED = "mastered"  # 已掌握
    ARCHIVED = "archived"  # 归档


# 定时任务状态
class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 课程状态
class CourseStatus:
    PLANNED = "planned"
    READY = "ready"  # 已备课就绪
    ONGOING = "ongoing"
    FINISHED = "finished"
    CANCELLED = "cancelled"


# 笔记本来源
class NotebookSource:
    IMAGE = "image"
    TEXT = "text"
    IMPORT = "import"


# 学段
class Stage:
    PRIMARY = "primary"  # 小学
    JUNIOR = "junior"  # 初中
    SENIOR = "senior"  # 高中
    UNIVERSITY = "university"  # 大学/考研


class Student(SQLModel, table=True):
    """学生档案（本体）。头像画像等衍生数据在后续扩展。"""

    id: int | None = Field(default=None, primary_key=True)
    external_id: str = Field(index=True, description="渠道侧唯一标识，如 QQ openid")
    nickname: str = ""
    stage: str = Stage.JUNIOR  # 学段，由画像驱动更新
    region_name: str = ""  # 地区/学校，用于校本同步（可选）
    tags: str = "[]"  # JSON 数组
    note: str = ""  # 人工备注
    created_at: datetime = Field(default_factory=utcnow)


class KnowledgePoint(SQLModel, table=True):
    """知识点掌握度。归属：the 学生 × 学科 → 章节 → 知识点。"""

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(index=True, foreign_key="student.id")
    subject: str = Field(index=True)
    chapter: str = ""
    name: str
    mastery: float = Field(default=0.0, ge=0.0, le=1.0, description="掌握度 0-1")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="该掌握度置信度")
    last_practice_at: datetime | None = None
    error_distribution: str = "{}"  # JSON：错误类型 -> 次数
    created_at: datetime = Field(default_factory=utcnow)


class WrongQuestion(SQLModel, table=True):
    """错题本条目。是错题闭环的存储核心。"""

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(index=True, foreign_key="student.id")
    subject: str = Field(index=True)
    knowledge_point_id: int | None = Field(default=None, foreign_key="knowledgepoint.id")
    source: str = "photo"  # photo / text / homework
    question_text: str = ""
    image_path: str = ""  # 图片落盘路径（工作区内）
    student_answer: str = ""  # 学生作答
    correct_answer: str = ""  # 正确答案
    analysis: str = ""  # 方法卡片/讲解，供复习引用
    error_type: str = "unknown"  # 概念不清/计算错误/审题失误/...
    status: str = WrongStatus.TO_REVIEW
    times_wrong: int = Field(default=1, ge=0)
    last_review_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class QuestionItem(SQLModel, table=True):
    """题集条目（学生专属题库）：错题 + 好题。

    与错题本（WrongQuestion，走复习闭环）互补：题集是「值得重做/重看的题」的收藏。
    kind=error 错题（按录入策略收录）、kind=good 好题（Agent 自主判定的经典/典型/一题多解题）。
    """

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(index=True, foreign_key="student.id")
    kind: str = Field(index=True, default="good")  # error / good
    subject: str = ""  # 学科
    knowledge_point: str = ""  # 知识点，格式『学科/章节/名称』
    question_text: str = ""  # 题干（文字形式，检索用）
    image_path: str = ""  # 题图路径（工作区内，拍照题）
    reason: str = ""  # 录入理由（为什么值得收）
    source: str = "tutoring"  # tutoring 讲题中 / import 题集导入
    created_at: datetime = Field(default_factory=utcnow)


class CoursePlan(SQLModel, table=True):
    """课程方案。kind=single 单次课；series 系列课（系统教学）。"""

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(index=True, foreign_key="student.id")
    kind: str = "single"  # single / series
    title: str
    subject: str = ""
    goal: str = ""  # 学生目标（系列课）
    schedule: str = "{}"  # JSON：排课日历
    status: str = CourseStatus.PLANNED
    created_at: datetime = Field(default_factory=utcnow)


class CourseInstance(SQLModel, table=True):
    """单次课实例（一次具体授课）。"""

    id: int | None = Field(default=None, primary_key=True)
    plan_id: int = Field(index=True, foreign_key="courseplan.id")
    student_id: int = Field(index=True, foreign_key="student.id")
    title: str = ""
    start_at: datetime = Field(index=True)
    end_at: datetime | None = None
    lecture_path: str = ""  # 备课讲义落盘路径
    status: str = CourseStatus.PLANNED
    created_at: datetime = Field(default_factory=utcnow)


class ScheduleTask(SQLModel, table=True):
    """统一调度任务。备课/提醒/开课/下课/复习推送/周报 全部落地为一条记录。"""

    id: int | None = Field(default=None, primary_key=True)
    student_id: int | None = Field(default=None, index=True, foreign_key="student.id")
    kind: str = Field(index=True)  # prepare / reminder / class_start / class_end / review / report
    fire_at: datetime = Field(index=True)
    payload: str = "{}"  # JSON 载荷（如引用课程/任务 ID）
    status: str = TaskStatus.PENDING
    last_run_at: datetime | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class NotebookEntry(SQLModel, table=True):
    """笔记本条目。"""

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(index=True, foreign_key="student.id")
    notebook: str = Field(index=True, description="笔记本名称，如『函数』")
    subject: str = ""
    topic: str = ""
    summary: str = ""
    content: str = ""
    source: str = NotebookSource.IMAGE
    source_path: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class KnowledgeCard(SQLModel, table=True):
    """方法卡片（知识库·方法库）。

    产品规格书 4.3 双库结构中的「方法库」：题型→方法→步骤→易错点。
    语义去重合并后入库，供解题时检索引用（越用越强）。
    本期先做精确/关键词检索，向量化检索在嵌入服务接入后增强。
    """

    id: int | None = Field(default=None, primary_key=True)
    student_id: int | None = Field(
        default=None, index=True, foreign_key="student.id", description="归属学生；为空表示公共方法卡"
    )
    subject: str = Field(index=True)
    question_type: str = ""  # 题型，如「二次函数求根」
    method: str = ""  # 方法总述
    steps: str = ""  # 步骤（换行分隔）
    pitfalls: str = ""  # 易错点（换行分隔）
    source: str = ""  # 从哪次解题提炼
    created_at: datetime = Field(default_factory=utcnow)


class PendingRecord(SQLModel, table=True):
    """待确认的错题记录（跨轮持久化）。

    当解题后是「询问」策略（新题/中难题做错，不宜直接记）时，把这道题暂存于此；
    学生下一轮确认记录时，由入口确定性写入错题本并清除。
    避免「询问→确认→记录」依赖模型记忆，保证闭环。
    """

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(index=True, foreign_key="student.id")
    payload: str = "{}"  # JSON：wrongbook 记录所需参数
    created_at: datetime = Field(default_factory=utcnow)


class WorkingContext(SQLModel, table=True):
    """学生的跨轮工作上下文（每个学生一行）。

    - ``current_problem``：最近正在讲解的题目（题干/答案/方法/知识点），供后续轮次引用。
    - ``long_pref``：长内容偏好（split/lecture/unset），由首次询问后落定。
    用「学生一行」的新表（additive），避免改动既有表结构。
    """

    student_id: int = Field(primary_key=True, foreign_key="student.id")
    current_problem: str = "{}"  # JSON
    long_pref: str = "unset"  # split / lecture / unset
    updated_at: datetime = Field(default_factory=utcnow)


class CorpusEntry(SQLModel, table=True):
    """知识库·语料库条目（讲义/教材/百科等，检索用）。

    与 :class:`KnowledgeCard`（方法库，解题沉淀）互补，构成规格 4.3 的「双库结构」。
    面向大段教学资料，供讲解时检索引用。向量检索在嵌入服务接入后增强，当前关键词回退。
    """

    id: int | None = Field(default=None, primary_key=True)
    student_id: int | None = Field(
        default=None, index=True, foreign_key="student.id", description="归属学生；空为公共语料"
    )
    subject: str = Field(index=True)
    title: str = ""
    content: str = ""
    source: str = ""  # lecture / textbook / web / import
    tags: str = "[]"  # JSON 数组
    created_at: datetime = Field(default_factory=utcnow)


class Session(SQLModel, table=True):
    """一段对话会话（无感分段后自动开新段）。"""

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(index=True, foreign_key="student.id")
    topic: str = ""  # 本段主题，由 Agent 总结
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    """会话内单条消息。L1 记忆层用。"""

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(index=True, foreign_key="session.id")
    role: str  # user / assistant / tool
    content: str
    created_at: datetime = Field(default_factory=utcnow)
