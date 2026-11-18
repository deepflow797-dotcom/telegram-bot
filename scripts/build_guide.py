"""Build the Arabic PDF guide (owner + customer, multi-account + free trial).

Usage
-----
    python scripts/build_guide.py [--out docs/guides/bot-guide-ar.pdf]

Requires ``reportlab``, ``arabic-reshaper`` and ``python-bidi`` (dev extras),
plus a font with Arabic coverage (DejaVu Sans is used by default).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ROOT / "docs" / "guides"

BOT_URL = "https://t.me/MY_RENTALS_2026_BOT"
BOT_NAME = "إدارة الإيجار"

INK = colors.HexColor("#12312c")
TEAL = colors.HexColor("#0f766e")
TEAL_LIGHT = colors.HexColor("#e6f4f1")
AMBER = colors.HexColor("#b45309")
AMBER_LIGHT = colors.HexColor("#fdf3e3")
GREY = colors.HexColor("#5b6b68")
LINE = colors.HexColor("#cfdedb")

FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
    ("/usr/share/fonts/truetype/freefont/FreeSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
]


def register_fonts() -> tuple[str, str]:
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists():
            pdfmetrics.registerFont(TTFont("Arabic", regular))
            pdfmetrics.registerFont(TTFont("Arabic-Bold", bold if Path(bold).exists() else regular))
            return "Arabic", "Arabic-Bold"
    raise SystemExit("no Arabic-capable font found")


REGULAR, BOLD = register_fonts()


#: Emoji have no glyph in the reportlab fonts, so they are dropped from the PDF.
_EMOJI_RE = re.compile(
    "[" "\U0001F000-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF"
    "\U00002300-\U000023FF" "\u2B00-\u2BFF" "\u25A0-\u25FF" "\u2190-\u21FF"
    "\uFE0F\u200D\u20E3" "]+"
)


def clean(text: str) -> str:
    """Drop emoji / guillemets (neutral chars break RTL layout) and collapse spaces."""
    text = _EMOJI_RE.sub("", text).replace("«", "").replace("»", "")
    return re.sub(r"\s+", " ", text).strip()


def ar(text: str, base_dir: str = "R") -> str:
    """Shape + bidi-reorder one *line* of Arabic text for reportlab."""
    return "\u200f" + get_display(arabic_reshaper.reshape(text), base_dir=base_dir)


def wrap_lines(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap on the *logical* text, so RTL line order stays correct."""
    lines: list[str] = []
    current = ""
    for word in clean(text).split():
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(ar(candidate), font, size) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


STYLES = {
    "title": ParagraphStyle(
        "title", fontName=BOLD, fontSize=30, leading=42, alignment=TA_CENTER, textColor=INK
    ),
    "subtitle": ParagraphStyle(
        "subtitle", fontName=REGULAR, fontSize=13, leading=24, alignment=TA_CENTER, textColor=GREY
    ),
    "h1": ParagraphStyle(
        "h1", fontName=BOLD, fontSize=17, leading=28, alignment=TA_RIGHT, textColor=TEAL,
        spaceBefore=2, spaceAfter=4,
    ),
    "h2": ParagraphStyle(
        "h2", fontName=BOLD, fontSize=13.5, leading=24, alignment=TA_RIGHT, textColor=INK,
        spaceBefore=6, spaceAfter=2,
    ),
    "body": ParagraphStyle(
        "body", fontName=REGULAR, fontSize=11, leading=19, alignment=TA_RIGHT, textColor=INK
    ),
    "bullet": ParagraphStyle(
        "bullet", fontName=REGULAR, fontSize=11, leading=19, alignment=TA_RIGHT, textColor=INK,
        rightIndent=6, spaceAfter=1.5,
    ),
    "cell": ParagraphStyle("cell", fontName=REGULAR, fontSize=10, leading=16, alignment=TA_RIGHT),
    "cellBold": ParagraphStyle("cellBold", fontName=BOLD, fontSize=10, leading=16, alignment=TA_RIGHT),
    "note": ParagraphStyle(
        "note", fontName=REGULAR, fontSize=10.5, leading=18, alignment=TA_RIGHT, textColor=AMBER
    ),
    "caption": ParagraphStyle(
        "caption", fontName=REGULAR, fontSize=9, leading=14, alignment=TA_CENTER, textColor=GREY
    ),
}


class Rule(Flowable):
    """Thin horizontal separator."""

    def __init__(self, width: float, color=LINE, thickness: float = 0.8) -> None:
        super().__init__()
        self.width = width
        self.color = color
        self.thickness = thickness

    def wrap(self, availWidth, availHeight):  # noqa: N802 - reportlab API
        return availWidth, self.thickness + 6

    def draw(self) -> None:
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 3, self.width, 3)


class SectionHeader(Flowable):
    """Coloured band with the (already shaped) section title."""

    def __init__(self, text: str, width: float, number: str | None = None) -> None:
        super().__init__()
        self.text = ar(text)
        self.number = number
        self.width = width
        self.height = 30

    def wrap(self, availWidth, availHeight):  # noqa: N802
        self.width = availWidth
        return availWidth, self.height + 8

    def draw(self) -> None:
        canvas = self.canv
        canvas.setFillColor(TEAL_LIGHT)
        canvas.roundRect(0, 4, self.width, self.height, 7, stroke=0, fill=1)
        canvas.setFillColor(TEAL)
        canvas.roundRect(self.width - 5, 4, 5, self.height, 0, stroke=0, fill=1)
        canvas.setFillColor(INK)
        canvas.setFont(BOLD, 15)
        canvas.drawRightString(self.width - 14, 13, self.text)
        if self.number:
            canvas.setFont(BOLD, 12)
            canvas.setFillColor(TEAL)
            canvas.drawString(14, 13, self.number)


def rtl(text: str, style: str = "body", *, width: float | None = None) -> Paragraph:
    st = STYLES[style]
    lines = wrap_lines(text, st.fontName, st.fontSize, width or FRAME_WIDTH)
    return Paragraph("<br/>".join(ar(line) for line in lines), st)


def ltr(text: str, style: str = "body") -> Paragraph:
    """Latin-only paragraph (commands, urls) — never reshaped."""
    return Paragraph(text, STYLES[style])


class RuleLine(Flowable):
    """A thin horizontal rule that spans the frame width."""

    def __init__(self, color: str = TEAL, thickness: float = 0.6) -> None:
        super().__init__()
        self.color = color
        self.thickness = thickness
        self.width = FRAME_WIDTH
        self.height = thickness

    def draw(self) -> None:
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)


def para(text: str, style: str = "body") -> Paragraph:
    return rtl(text, style)


BULLET_COL = 6 * mm


def bullet(text: str) -> Table:
    """Bullet marker in its own (right-hand) column — reliable in an RTL layout."""
    marker = Paragraph(ar("•"), STYLES["bullet"])
    body = rtl(text, "bullet", width=FRAME_WIDTH - BULLET_COL - 4)
    table = Table(
        [[body, marker]], colWidths=[FRAME_WIDTH - BULLET_COL, BULLET_COL]
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
    )
    return table


def note(text: str) -> Table:
    inner = rtl(text, "note", width=FRAME_WIDTH - 20)
    table = Table([[inner]], colWidths=[FRAME_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), AMBER_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.6, AMBER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def grid(
    header: list[str],
    rows: list[list[str]],
    widths: list[float] | None = None,
    *,
    ltr_columns: tuple[int, ...] = (),
) -> Table:
    col_widths = widths or [FRAME_WIDTH / len(header)] * len(header)

    def cell(value: str, column: int, bold: bool = False) -> Paragraph:
        style = "cellBold" if bold else "cell"
        if column in ltr_columns:
            return Paragraph(value, STYLES[style])
        return rtl(value, style, width=col_widths[column] - 16)

    # reportlab lays tables out left-to-right, so column 0 is rendered last
    # (rightmost) to keep the Arabic reading order: first column on the right.
    data = [[cell(value, i, bold=True) for i, value in enumerate(header)]]
    data += [[cell(value, i) for i, value in enumerate(row)] for row in rows]
    data = [list(reversed(row)) for row in data]
    table = Table(data, colWidths=list(reversed(col_widths)), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TEAL_LIGHT),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fbfa")]),
            ]
        )
    )
    return table


PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
FRAME_WIDTH = PAGE_W - 2 * MARGIN
FRAME_HEIGHT = PAGE_H - 34 * mm


def decorate(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(TEAL)
    canvas.rect(0, PAGE_H - 9 * mm, PAGE_W, 9 * mm, stroke=0, fill=1)
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, 16 * mm, PAGE_W - MARGIN, 16 * mm)
    canvas.setFont(REGULAR, 8.5)
    canvas.setFillColor(GREY)
    canvas.drawString(MARGIN, 11.5 * mm, BOT_URL)
    canvas.drawRightString(PAGE_W - MARGIN, 11.5 * mm, ar(f"صفحة {doc.page}"))
    canvas.restoreState()


EDITION_DATE = dt.date.today()


def cover() -> list[Flowable]:
    story: list[Flowable] = [Spacer(1, 16 * mm)]
    story.append(Paragraph(ar("بوت إدارة الإيجارات"), STYLES["title"]))
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            ar("دليل الاستخدام الكامل — للمالك وللعميل"),
            STYLES["subtitle"],
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(Rule(FRAME_WIDTH))
    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            ar("منصة واحدة تخدم عدة عملاء: كل حساب ببياناته المستقلة، "
               "وتجربة مجانية 7 أيام لكل حساب جديد."),
            STYLES["body"],
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(
        note(
            "هذا الإصدار يشمل: تصدير كل البيانات في ملف إكسل، والحذف النهائي إلى جانب الأرشفة، "
            "وتحكّم كل حساب في الحذف والأرشفة من الإعدادات."
        )
    )
    story.append(Spacer(1, 6 * mm))

    qr_path = GUIDES / "bot-qr-ar.png"
    if qr_path.exists():
        story.append(Image(str(qr_path), width=52 * mm, height=52 * mm))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(ar("امسح الرمز بكاميرا تيليجرام لفتح البوت"), STYLES["caption"]))
    story.append(Spacer(1, 6 * mm))

    info = grid(
        ["البند", "التفاصيل"],
        [
            ["اسم البوت", BOT_NAME],
            ["رابط الفتح", BOT_URL],
            ["رقم التواصل", "22222222 (قابل للتغيير من داخل البوت)"],
            ["التجربة المجانية", "7 أيام لكل حساب جديد (قابلة للتعديل)"],
            ["إصدار الدليل", EDITION_DATE.isoformat()],
        ],
        widths=[45 * mm, FRAME_WIDTH - 45 * mm],
    )
    story.append(info)
    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            ar("تنبيه: هذا البوت منتج يُباع بالاشتراك، وليس للاستخدام الشخصي فقط. "
               "كل عميل يعمل على بيانات منفصلة تماماً عن بيانات المالك وعن بقية العملاء."),
            STYLES["body"],
        )
    )
    return story


def contents() -> list[Flowable]:
    story: list[Flowable] = [SectionHeader("محتويات الدليل", FRAME_WIDTH)]
    rows = [
        ["1", "ما هو البوت ومن يستخدمه"],
        ["2", "تجربة المالك: أول دخول والقائمة الرئيسية"],
        ["3", "التجربة المجانية 7 أيام للحساب الجديد"],
        ["4", "تغيير مدة التجربة من داخل البوت"],
        ["5", "طلبات الاشتراك: الموافقة والرفض (عند إيقاف التجربة)"],
        ["6", "الاشتراك، التجديد، والتجميد عند الانتهاء"],
        ["7", "شاشات المالك الثلاث (الطلبات، الحسابات، التواصل)"],
        ["8", "تجربة العميل خطوة بخطوة"],
        ["9", "العزل والأمان: كيف لا يرى عميل بيانات غيره"],
        ["10", "التقرير اليومي وتصدير ملف إكسل"],
        ["11", "خريطة الأزرار والأوامر"],
        ["12", "أسئلة شائعة وأخطاء متوقعة"],
    ]
    story.append(grid(["#", "الموضوع"], rows, widths=[16 * mm, FRAME_WIDTH - 16 * mm]))
    story += [
        Spacer(1, 6 * mm),
        note(
            "كيف تستخدم هذا الدليل؟ المالك يقرأ الأقسام 2 إلى 7، والعميل يقرأ الأقسام 3 و8. "
            "القسمان 11 و12 مرجعان سريعان لأزرار البوت والأخطاء المتوقعة."
        ),
        Spacer(1, 4 * mm),
        grid(
            ["المصطلح", "المعنى في هذا الدليل"],
            [
                ["المالك", "أنت: صاحب المنصة الذي يفعّل الحسابات ويضبط التجربة والتواصل"],
                ["العميل", "مشترك يستخدم البوت لإدارة عقاراته، ببيانات منفصلة تماماً"],
                ["التجربة المجانية", "مدة يفتح فيها البوت للعميل الجديد تلقائياً (7 أيام افتراضياً)"],
                ["التجميد", "إيقاف العمليات عند انتهاء المدة مع الحفاظ على كل البيانات"],
            ],
            widths=[40 * mm, FRAME_WIDTH - 40 * mm],
        ),
    ]
    return story


def section(number: str, title: str, blocks: list[Flowable]) -> list[Flowable]:
    return [
        CondPageBreak(72 * mm),
        Spacer(1, 5 * mm),
        SectionHeader(title, FRAME_WIDTH, number),
        Spacer(1, 2 * mm),
        *blocks,
    ]


def build_story() -> list[Flowable]:
    story = cover()
    story.append(PageBreak())
    story += contents()
    story.append(PageBreak())

    story += section(
        "1",
        "ما هو البوت ومن يستخدمه",
        [
            para(
                "بوت تيليجرام عربي لإدارة إيجار العقارات: المباني، الشقق، المستأجرون، "
                "تسجيل الدفعات (كاملة أو جزئية)، متابعة المتأخرات والاستحقاقات، وتقرير يومي تلقائي."
            ),
            Spacer(1, 2 * mm),
            bullet("المالك (أنت): يدير المنصة نفسها، يشاهد طلبات الاشتراك والحسابات، ويضبط التجربة المجانية وبيانات التواصل."),
            bullet("العميل (المشترك): يستخدم البوت لإدارة عقاراته فقط، ولا يرى أي شاشة إدارية أو بيانات غيره."),
            Spacer(1, 2 * mm),
            para(
                "كل شيء يعمل بالأزرار العربية داخل تيليجرام، بدون أوامر. وهناك أوامر اختصار اختيارية "
                "مذكورة في القسم 11 (البدء، القائمة، الإلغاء، المساعدة، معرّفي)."
            ),
            Spacer(1, 3 * mm),
            note(
                "مهم: بيانات المالك وبيانات كل عميل محفوظة في مساحة مستقلة داخل قاعدة البيانات. "
                "لا يوجد أي طريق يظهر فيه مبنى عميل عند عميل آخر."
            ),
        ],
    )

    story += section(
        "2",
        "تجربة المالك: أول دخول والقائمة الرئيسية",
        [
            bullet("افتح البوت من حسابك المسجّل مسبقاً كمالك، واضغط زر البدء."),
            bullet("تظهر لك القائمة الرئيسية: العقارات، المستأجرون، تسجيل الدفع، التقارير، البحث، الإعدادات."),
            bullet("بالإضافة إلى ذلك يظهر لك صف إداري خاص بالمالك: طلبات الاشتراك، الحسابات، بيانات التواصل، التجربة المجانية."),
            bullet("أزرارك الإدارية لا تظهر لأي عميل آخر، وأي محاولة للوصول إليها من حساب آخر تُرفض."),
            Spacer(1, 3 * mm),
            grid(
                ["الزر", "ماذا يفعل"],
                [
                    ["🏠 إدارة العقارات", "إضافة وتعديل المباني والشقق"],
                    ["👥 المستأجرون", "ملفات المستأجرين، البحث، الأرشفة"],
                    ["💰 تسجيل الدفع", "دفعة كاملة أو جزئية مع شاشة تأكيد"],
                    ["📊 التقارير", "اليوم، الشهر، المتأخرات، الاستحقاقات، الإيرادات"],
                    ["⚙️ الإعدادات", "العملة، وقت التقرير اليومي، تشغيل أو إيقاف التقرير، الحذف والأرشفة"],
                ],
                widths=[55 * mm, FRAME_WIDTH - 55 * mm],
            ),
        ],
    )

    story += section(
        "3",
        "التجربة المجانية 7 أيام للحساب الجديد",
        [
            para(
                "أول مرة يُشغَّل فيها البوت من أي حساب تيليجرام جديد، لا ينتظر هذا الحساب موافقتك: "
                "يفتح له البوت تجربة مجانية كاملة تلقائياً، مدتها 7 أيام افتراضياً."
            ),
            Spacer(1, 2 * mm),
            bullet("يصل للعميل فوراً رسالة ترحيب فيها: مدة التجربة، تاريخ الانتهاء، ورقم التواصل للتجديد."),
            bullet("يُجهَّز له فوراً مساحة بيانات فارغة خاصة به، فيبدأ بإضافة مبانٍه وشققّه ومستأجريه."),
            bullet("يصلك أنت إشعار: «حساب جديد بدأ التجربة المجانية» مع الاسم واليوزر ورقم الحساب وتاريخ الانتهاء، "
                   "ومعه أزرار: ترقية 30 يوماً، ترقية 90 يوماً، إيقاف."),
            bullet("إذا أعاد العميل تشغيل البوت أثناء التجربة، لا تُعاد التجربة ولا تُصفَّر مدتها."),
            bullet("عند انتهاء التجربة يتوقف البوت عن العمليات ويظهر للعميل شاشة تجديد، وبياناته تبقى محفوظة كما هي."),
            Spacer(1, 2 * mm),
            note(
                "الحساب الذي انتهت تجربته يبقى ظاهراً في قائمة الحسابات بحالة «موقوف»، وبضغطة واحدة تمدده أو تفعله من جديد."
            ),
        ],
    )

    story += section(
        "4",
        "تغيير مدة التجربة من داخل البوت",
        [
            para("تستطيع تغيير مدة التجربة في أي وقت، من حسابك فقط، بدون أي تعديل في الكود:"),
            Spacer(1, 2 * mm),
            bullet("من القائمة الإدارية اختر «🎁 التجربة المجانية»."),
            bullet("اضغط «✏️ تغيير مدة التجربة»."),
            bullet("أرسل عدد الأيام (رقم من 0 إلى 365) — مثال: 7 أو 14 أو 30."),
            bullet("تُطبَّق المدة على كل حساب جديد يدخل بعد التعديل، ولا تغيّر مدد الحسابات الحالية."),
            Spacer(1, 2 * mm),
            grid(
                ["القيمة", "النتيجة"],
                [
                    ["7 (الافتراضي)", "كل حساب جديد يبدأ بتجربة 7 أيام"],
                    ["14 أو 30", "تجربة أطول لجذب العملاء"],
                    ["0", "إيقاف التجربة: كل حساب جديد يظهر لك كـ«طلب» تنتظر موافقتك"],
                ],
                widths=[40 * mm, FRAME_WIDTH - 40 * mm],
            ),
            Spacer(1, 2 * mm),
            note("إذا أراد العميل تمديداً، استخدم زر «➕ تمديد» من شاشة بيانات حسابه؛ التمديد يضاف على تاريخ الانتهاء الحالي."),
        ],
    )

    story += section(
        "5",
        "طلبات الاشتراك: الموافقة والرفض (عند إيقاف التجربة)",
        [
            para(
                "إذا كانت التجربة معطّلة (0 يوم)، يصبح كل حساب جديد «طلب» بدل تفعيل تلقائي، وتتحكم أنت كلياً:"
            ),
            Spacer(1, 2 * mm),
            bullet("العميل يرى شاشة: «هذا البوت بالاشتراك» مع رقم تواصلك ورسالتك التي تطالب فيها بإرسال اسم المستخدم."),
            bullet("يصلك إشعار فيه الاسم واليوزر ورقم الحساب، ومعه: تفعيل 30 يوماً، أو تفعيل 90 يوماً، أو رفض."),
            bullet("بالضغط على «تفعيل» تُجهَّز للعميل مساحته الفارغة ويصله إشعار التفعيل مباشرة."),
            bullet("بالضغط على «رفض» يصله إشعار الرفض، ولا تُجهَّز له أي مساحة بيانات."),
            Spacer(1, 2 * mm),
            para("يمكنك أيضاً فتح «📥 طلبات الاشتراك» في أي وقت لمراجعة كل الطلبات المعلّقة بضغطة واحدة."),
        ],
    )

    story += section(
        "6",
        "الاشتراك، التجديد، والتجميد عند الانتهاء",
        [
            bullet("الاشتراك المدفوع يحدد تاريخ انتهاء (30 أو 90 يوماً حسب ما تختاره)."),
            bullet("قبل الانتهاء يرى العميل عدد الأيام المتبقية في بيانات حسابه عندك، ويستطيع سؤالك عن التجديد."),
            bullet("عند الانتهاء: يتوقف البوت للعميل — لا يمكنه إضافة أو تعديل أو تسجيل دفعات — وتظهر له شاشة تجديد فيها رقم تواصلك."),
            bullet("البيانات لا تُحذف ولا تُخفى: تعود كما هي بمجرد التجديد."),
            bullet("التجديد من عندك: «👥 الحسابات» ← افتح الحساب ← «➕ تمديد»، ويصل إشعار للعميل."),
            Spacer(1, 3 * mm),
            note("المالك لا ينتهي اشتراكه أبداً، ولا تظهر له شاشات الدفع أو التجديد إطلاقاً."),
        ],
    )

    story += section(
        "7",
        "شاشات المالك الثلاث",
        [
            grid(
                ["الشاشة", "ماذا تعرض", "أهم الأزرار"],
                [
                    ["📥 طلبات الاشتراك", "كل الحسابات الجديدة المعلّقة", "تفعيل 30 أو 90 يوماً، أو رفض"],
                    ["👥 الحسابات", "كل الحسابات وحالتها وتاريخ انتهائها", "بيانات الحساب، تمديد، إيقاف/تفعيل"],
                    ["📞 بيانات التواصل", "رقم التواصل ورسالة الطلب ومدة التجربة", "تعديل الرقم، تعديل الرسالة، مدة التجربة"],
                ],
                widths=[46 * mm, 62 * mm, FRAME_WIDTH - 108 * mm],
            ),
            Spacer(1, 3 * mm),
            bullet("رقم التواصل الافتراضي 22222222، وتغييره يتم من داخل البوت ويظهر فوراً لكل عميل."),
            bullet("حساب التجربة يظهر في القائمة بعلامة 🎁 بجانب حالته لتفرّقه عن المشتركين المدفوعين."),
            bullet("«⏸ إيقاف» توقف حساباً مشتركاً فوراً (لعدم السداد مثلاً) دون حذف بياناته."),
        ],
    )

    story += section(
        "8",
        "تجربة العميل خطوة بخطوة",
        [
            bullet("يفتح رابط البوت أو يمسح رمز QR، ثم يضغط زر البدء."),
            bullet("إن كانت التجربة مفعّلة: يقرأ رسالة التجربة ويبدأ فوراً في إضافة مبناه الأول."),
            bullet("إن كانت التجربة متوقفة: يرى شاشة الاشتراك ويرسل اسم المستخدم إلى رقمك حتى تفعّله."),
            bullet("يعمل على عقاراته فقط: لا يرى أي زر إداري، ولا أي بيانات من حساب آخر."),
            bullet("يستطيع تغيير عملته ووقت تقريره من ⚙️ الإعدادات (تخصّه وحده)."),
            bullet("عند انتهاء المدة يرى شاشة التجديد، وبياناته تبقى بينها مبانٍه ومستأجروه وسجل دفعاته."),
        ],
    )

    story += section(
        "9",
        "العزل والأمان",
        [
            para(
                "كل حساب يعمل على مخطط مستقل داخل قاعدة البيانات (مخطط المالك، ثم مخطط لكل عميل على حدة). "
                "قبل أي استعلام يوجَّه الاتصال إلى مخطط الحساب النشط فقط، فلا يمكن لعملية أن تقرأ صفوف عميل آخر "
                "حتى لو أخطأ الكود في شرط التصفية."
            ),
            Spacer(1, 2 * mm),
            bullet("مفتاح تيليجرام غير المفعّل لا يصل إلى أي شاشة عمل، ولا إلى أي استعلام بيانات."),
            bullet("الشاشات الإدارية محمية بفحص صفة المالك قبل كل تنفيذ، وليست مخفية بالأزرار فقط."),
            bullet("كل جدول محمي بسياسة أمان على مستوى الصفوف، فلا يمكن قراءة البيانات عبر واجهة قاعدة البيانات العامة."),
            bullet("نقطة الويب هوك محمية بسر، ونقاط المهام المجدولة محمية بمفتاح، ولا تُسجَّل الأسرار في السجلات."),
            bullet("سجل تدقيق يسجّل إضافة المباني والشقق والمستأجرين والدفعات وتغيير الإعدادات."),
        ],
    )

    story += section(
        "10",
        "التقرير اليومي وتصدير ملف إكسل",
        [
            bullet("يُرسل التقرير اليومي تلقائياً في الوقت المحدد (الافتراضي 08:00 بتوقيت نواكشوط)."),
            bullet("كل حساب يستلم تقريره هو فقط، ويحتوي بياناته: الملخص وقوائم المستأجرين (مغطى، قريب، مستحق اليوم، متأخر)."),
            bullet("تقرير واحد فقط لكل تاريخ، مع إعادة محاولة عند فشل الإرسال."),
            bullet("يمكن تعطيل التقرير لحساب واحد من ⚙️ الإعدادات دون التأثير على بقية الحسابات."),
            Spacer(1, 2 * mm),
            Paragraph(ar("تصدير ملف إكسل"), STYLES["h2"]),
            bullet("من 📊 التقارير اضغط «📗 تصدير كل البيانات Excel» فيصلك ملف .xlsx جاهز للتنزيل."),
            bullet("الملف يحتوي ست أوراق: ملخص، المباني، الشقق، المستأجرون، الدفعات، المتأخرات."),
            bullet("الأرقام مكتوبة كأرقام لا كنصوص، فتستطيع الجمع والتصفية داخل Excel مباشرة."),
            bullet("الملف يُبنى من بيانات حسابك وحده، ويُفتح على Excel أو Google Sheets مع اتجاه من اليمين إلى اليسار."),
        ],
    )

    story += section(
        "11",
        "خريطة الأزرار والأوامر",
        [
            grid(
                ["القسم", "الأزرار / الأوامر"],
                [
                    ["العقارات", "إضافة مبنى • عرض المباني • شقة جديدة • بطاقة المبنى أو الشقة • تعديل • أرشفة • حذف نهائي"],
                    ["المستأجرون", "مستأجر جديد • قائمة المستأجرين • ملف المستأجر • تعديل • أرشفة • حذف نهائي"],
                    ["الدفع", "اختيار المستأجر ← المبلغ ← ملاحظة ← شاشة تأكيد ← تسجيل"],
                    ["التقارير", "تقرير اليوم • الشهر • سجل الدفعات • المتأخرات • الإيرادات • تصدير Excel"],
                    ["المالك", "📥 طلبات الاشتراك • 👥 الحسابات • 📞 بيانات التواصل • 🎁 التجربة المجانية"],
                    ["أوامر اختصار", "البدء، القائمة، الإلغاء، المساعدة، معرّفي (انظر أسفل الجدول)"],
                ],
                widths=[34 * mm, FRAME_WIDTH - 34 * mm],
            ),
            Spacer(1, 2 * mm),
            Table(
                [
                    [
                        Paragraph(f"<b>{cmd}</b>", STYLES["cell"])
                        for cmd in ("/start", "/menu", "/cancel", "/help", "/id")
                    ]
                ],
                colWidths=[FRAME_WIDTH / 5] * 5,
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), TEAL_LIGHT),
                        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                ),
            ),
            Spacer(1, 1 * mm),
            para("هذه أوامر اختيارية، وكل ما فيها متاح أيضاً بالأزرار."),
        ],
    )

    story += section(
        "12",
        "أسئلة شائعة وأخطاء متوقعة",
        [
            Paragraph(ar("عميل يقول: البوت لا يستجيب لي."), STYLES["h2"]),
            bullet("إن كان جديداً فسيرى رسالة التجربة فوراً، وإن ظهرت له شاشة «انتهى اشتراكك» فمدّد المدة من 👥 الحسابات."),
            Spacer(1, 1 * mm),
            Paragraph(ar("كيف أُعيد حساباً موقوفاً؟"), STYLES["h2"]),
            bullet("👥 الحسابات ← الحساب ← «➕ تمديد 30 يوم» أو «▶️ تفعيل»."),
            Spacer(1, 1 * mm),
            Paragraph(ar("هل يرى العميل بيانات عملاء آخرين؟"), STYLES["h2"]),
            bullet("لا. لكل حساب مساحة مستقلة، ولكل استعلام مسار بحث مقيد بحساب واحد."),
            Spacer(1, 1 * mm),
            Paragraph(ar("هل أستطيع إيقاف التجربة نهائياً؟"), STYLES["h2"]),
            bullet("نعم: 🎁 التجربة المجانية ← تغيير المدة ← أرسل 0."),
            Spacer(1, 1 * mm),
            Paragraph(ar("ما الفرق بين الأرشفة والحذف النهائي؟"), STYLES["h2"]),
            bullet("الأرشفة تُخفي السجل من القوائم وتحفظ كل شيء، والحذف النهائي يمحو السجل ولا يمكن استرجاعه."),
            bullet("يُمنع الحذف النهائي إذا كان للمبنى شقق، أو للشقة مستأجر، أو للمستأجر دفعات مسجلة."),
            bullet("تتحكم في الحذف والأرشفة من ⚙️ الإعدادات ← 🗑 الحذف والأرشفة، وتخص حسابك وحده."),
            Spacer(1, 2 * mm),
            RuleLine(),
            Spacer(1, 1 * mm),
            Paragraph(
                ar(
                    f'لأي استفسار أو تجديد: راسل المالك على الرقم 22222222 — '
                    f'<link href="{BOT_URL}">{BOT_URL}</link>'
                ),
                STYLES["body"],
            ),
        ],
    )
    return [item for item in story if item is not None]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Arabic PDF guide")
    parser.add_argument("--out", default=str(GUIDES / "bot-guide-ar.pdf"))
    parser.add_argument(
        "--date",
        default=None,
        help="edition date stamped on the cover (defaults to today)",
    )
    args = parser.parse_args(argv)

    global EDITION_DATE
    if args.date:
        EDITION_DATE = dt.date.fromisoformat(args.date)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=20 * mm,
        bottomMargin=22 * mm,
        title="دليل بوت إدارة الإيجارات",
        author=BOT_NAME,
        subject="دليل المالك والعميل — منصة متعددة الحسابات مع تجربة مجانية",
    )
    frame = Frame(
        MARGIN,
        22 * mm,
        FRAME_WIDTH,
        FRAME_HEIGHT,
        id="body",
        showBoundary=0,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[frame], onPage=decorate),
            PageTemplate(id="body", frames=[frame], onPage=decorate),
        ]
    )
    doc.build(build_story())
    print(f"wrote {out} ({os.path.getsize(out) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
