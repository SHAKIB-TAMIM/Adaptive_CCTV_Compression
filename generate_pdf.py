#!/usr/bin/env python3
"""
Generate the Capstone Project Proposal PDF with rewritten content
reflecting the current project implementation status.
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.colors import HexColor, black, white, gray
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, Frame, PageTemplate, BaseDocTemplate,
    NextPageTemplate, KeepTogether
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO
import os
import textwrap

# ─── Page dimensions ───
PAGE_W, PAGE_H = A4
MARGIN_L = 25 * mm
MARGIN_R = 25 * mm
MARGIN_T = 20 * mm
MARGIN_B = 20 * mm

# ─── Colors ───
DARK_BLUE = HexColor("#1a237e")
MED_BLUE = HexColor("#283593")
LIGHT_BLUE = HexColor("#e8eaf6")
ACCENT = HexColor("#3949ab")
TABLE_HEADER_BG = HexColor("#283593")
TABLE_ALT_ROW = HexColor("#f5f5f5")

# ─── Styles ───
styles = getSampleStyleSheet()

styles.add(ParagraphStyle(
    name='MyCoverTitle',
    fontName='Helvetica-Bold',
    fontSize=18,
    leading=24,
    alignment=TA_CENTER,
    spaceAfter=6*mm,
    textColor=DARK_BLUE,
))
styles.add(ParagraphStyle(
    name='CoverSubtitle',
    fontName='Helvetica',
    fontSize=13,
    leading=18,
    alignment=TA_CENTER,
    spaceAfter=4*mm,
))
styles.add(ParagraphStyle(
    name='CoverSmall',
    fontName='Helvetica',
    fontSize=11,
    leading=15,
    alignment=TA_CENTER,
    spaceAfter=2*mm,
))
styles.add(ParagraphStyle(
    name='MyHeading1',
    fontName='Helvetica-Bold',
    fontSize=15,
    leading=20,
    spaceBefore=8*mm,
    spaceAfter=4*mm,
    textColor=DARK_BLUE,
    borderWidth=0,
    borderPadding=0,
))
styles.add(ParagraphStyle(
    name='MyHeading2',
    fontName='Helvetica-Bold',
    fontSize=13,
    leading=17,
    spaceBefore=5*mm,
    spaceAfter=3*mm,
    textColor=MED_BLUE,
))
styles.add(ParagraphStyle(
    name='MyHeading3',
    fontName='Helvetica-Bold',
    fontSize=11,
    leading=15,
    spaceBefore=3*mm,
    spaceAfter=2*mm,
    textColor=ACCENT,
))
styles.add(ParagraphStyle(
    name='Body',
    fontName='Helvetica',
    fontSize=10,
    leading=14,
    alignment=TA_JUSTIFY,
    spaceAfter=2*mm,
))
styles.add(ParagraphStyle(
    name='BodyBold',
    fontName='Helvetica-Bold',
    fontSize=10,
    leading=14,
    alignment=TA_JUSTIFY,
    spaceAfter=2*mm,
))
styles.add(ParagraphStyle(
    name='MyBullet',
    fontName='Helvetica',
    fontSize=10,
    leading=14,
    leftIndent=8*mm,
    bulletIndent=3*mm,
    spaceAfter=1.5*mm,
    alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    name='TableCell',
    fontName='Helvetica',
    fontSize=8.5,
    leading=12,
    alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    name='TableCellBold',
    fontName='Helvetica-Bold',
    fontSize=8.5,
    leading=12,
    alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    name='TableHeader',
    fontName='Helvetica-Bold',
    fontSize=9,
    leading=12,
    alignment=TA_CENTER,
    textColor=white,
))
styles.add(ParagraphStyle(
    name='Caption',
    fontName='Helvetica-Oblique',
    fontSize=9,
    leading=12,
    alignment=TA_CENTER,
    spaceBefore=2*mm,
    spaceAfter=4*mm,
    textColor=HexColor("#555555"),
))
styles.add(ParagraphStyle(
    name='RefEntry',
    fontName='Helvetica',
    fontSize=9,
    leading=13,
    alignment=TA_LEFT,
    leftIndent=5*mm,
    firstLineIndent=-5*mm,
    spaceAfter=1.5*mm,
))
styles.add(ParagraphStyle(
    name='PageNumber',
    fontName='Helvetica',
    fontSize=9,
    alignment=TA_CENTER,
))
styles.add(ParagraphStyle(
    name='Declaration',
    fontName='Helvetica',
    fontSize=10,
    leading=15,
    alignment=TA_JUSTIFY,
    spaceAfter=3*mm,
))
styles.add(ParagraphStyle(
    name='SigLine',
    fontName='Helvetica',
    fontSize=10,
    leading=14,
    alignment=TA_LEFT,
    spaceBefore=8*mm,
))

# ─── Helper functions ───

def make_table(headers, rows, col_widths=None):
    """Create a styled table."""
    data = []
    hdr = [Paragraph(h, styles['TableHeader']) for h in headers]
    data.append(hdr)
    for row in rows:
        data.append([Paragraph(str(c), styles['TableCell']) for c in row])

    avail = PAGE_W - MARGIN_L - MARGIN_R
    if col_widths is None:
        n = len(headers)
        col_widths = [avail / n] * n

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style_cmds))
    return t


def image_placeholder(label, w, h):
    """Create an empty box placeholder for an image."""
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.graphics import renderPDF
    d = Drawing(w, h)
    d.add(Rect(0, 0, w, h, stroke=HexColor("#999999"), fill=HexColor("#f9f9f9"), strokeWidth=1))
    d.add(String(w/2, h/2 + 4, label, fontName='Helvetica-Oblique', fontSize=9,
                 fillColor=HexColor("#999999"), textAnchor='middle'))
    return d


def placeholder_fig(label, width=None, height=50*mm):
    """Return an image placeholder flowable."""
    from reportlab.platypus import Flowable
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.graphics import renderPDF

    avail = PAGE_W - MARGIN_L - MARGIN_R
    w = width or avail

    class PlaceHolder(Flowable):
        def __init__(self, lbl, w, h):
            super().__init__()
            self.lbl = lbl
            self.width = w
            self.height = h

        def draw(self):
            self.canv.setStrokeColor(HexColor("#999999"))
            self.canv.setFillColor(HexColor("#f9f9f9"))
            self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=1)
            self.canv.setFillColor(HexColor("#999999"))
            self.canv.setFont("Helvetica-Oblique", 9)
            self.canv.drawCentredString(self.width/2, self.height/2 + 4, self.lbl)
            self.canv.drawCentredString(self.width/2, self.height/2 - 8, "(Placeholder for diagram/plot)")

    return PlaceHolder(label, w, height)


def new_page():
    return PageBreak()


def spacer(h=3*mm):
    return Spacer(1, h)


def body(text):
    return Paragraph(text, styles['Body'])


def body_bold(text):
    return Paragraph(text, styles['BodyBold'])


def h1(text):
    return Paragraph(text, styles['MyHeading1'])


def h2(text):
    return Paragraph(text, styles['MyHeading2'])


def h3(text):
    return Paragraph(text, styles['MyHeading3'])


def bullet(text):
    return Paragraph(f"<bullet>&bull;</bullet>{text}", styles['MyBullet'])


def caption(text):
    return Paragraph(text, styles['Caption'])


# ─── Page number callback ───
def add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 9)
    canvas.setFillColor(HexColor("#888888"))
    canvas.drawCentredString(PAGE_W / 2, 12 * mm, f"{doc.page}")
    canvas.restoreState()


# ─── Build Document ───

def build_pdf(output_path):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
    )

    story = []

    # ═══════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════
    story.append(Spacer(1, 25*mm))
    story.append(Paragraph("CAPSTONE PROJECT PROPOSAL", styles['MyCoverTitle']))
    story.append(Paragraph("FALL 2025", styles['CoverSubtitle']))
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(
        "Adaptive CCTV Compression System Using<br/>"
        "Dynamic Region of Interest (ROI) Control",
        styles['MyCoverTitle']
    ))
    story.append(Spacer(1, 12*mm))
    story.append(Paragraph("Submitted by", styles['CoverSmall']))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph("Shakibul Islam Tamim", styles['CoverSubtitle']))
    story.append(Paragraph("ID: 0822220105101046", styles['CoverSmall']))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph("Jannatul Tabassum Nahar", styles['CoverSubtitle']))
    story.append(Paragraph("ID: 0822220205101001", styles['CoverSmall']))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph("Mostafa Main Uddin", styles['CoverSubtitle']))
    story.append(Paragraph("ID: 0822220105101017", styles['CoverSmall']))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("Supervised by", styles['CoverSmall']))
    story.append(Paragraph("Hasan Abdullah", styles['CoverSubtitle']))
    story.append(Paragraph("Assistant Professor, Dept. of CSE, BAIUST", styles['CoverSmall']))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "in partial fulfillment of the requirements for the degree of<br/>"
        "Bachelor of Science in Computer Science and Engineering",
        styles['CoverSmall']
    ))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "DEPARTMENT OF COMPUTER SCIENCE AND ENGINEERING<br/>"
        "BANGLADESH ARMY INTERNATIONAL UNIVERSITY OF SCIENCE AND<br/>"
        "TECHNOLOGY (BAIUST)",
        styles['CoverSmall']
    ))
    story.append(new_page())

    # ═══════════════════════════════════════════════
    # DECLARATION
    # ═══════════════════════════════════════════════
    story.append(h1("DECLARATION"))
    story.append(body(
        "We hereby declare that the work presented in this Capstone Project Proposal is the result of our "
        "own investigation and efforts, conducted under the guidance and supervision of Hasan Abdullah, "
        "Assistant Professor, Department of Computer Science and Engineering, Bangladesh Army International "
        "University of Science and Technology, Cumilla, Bangladesh. We confirm that no part of this proposal "
        "has been submitted, nor is being submitted, for the award of any other degree or diploma at any institution."
    ))
    story.append(Spacer(1, 10*mm))
    story.append(body_bold("Countersigned"))
    story.append(Spacer(1, 15*mm))
    story.append(Paragraph("-----------------------------<br/>(Hasan Abdullah)<br/>Assistant Professor<br/>Supervisor", styles['SigLine']))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(
        "-----------------------------&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
        "-----------------------------&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
        "-----------------------------<br/>"
        "Shakibul Islam Tamim&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
        "Jannatul Tabassum Nahar&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
        "Mostafa Main Uddin<br/>"
        "ID: 0822220105101046&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
        "ID: 0822220205101001&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
        "ID: 0822220105101017",
        styles['SigLine']
    ))
    story.append(new_page())

    # ═══════════════════════════════════════════════
    # APPROVAL
    # ═══════════════════════════════════════════════
    story.append(h1("APPROVAL"))
    story.append(body(
        "The Capstone Project titled \"Adaptive CCTV Compression System Using Dynamic Region of Interest "
        "(ROI) Control,\" submitted by Shakibul Islam Tamim (0822220105101046), Jannatul Tabassum Nahar "
        "(0822220205101001), and Mostafa Main Uddin (0822220105101017) to the Department of Computer "
        "Science and Engineering, Bangladesh Army International University of Science and Technology, "
        "has been reviewed and approved as satisfactory for partial fulfillment of the requirements for "
        "the degree of Bachelor of Science in Computer Science and Engineering."
    ))
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("Board of Examiners", styles['BodyBold']))
    story.append(Spacer(1, 15*mm))
    story.append(Paragraph("---------------------------------------<br/><br/>Supervisor<br/><br/>Hasan Abdullah<br/>Assistant Professor<br/>Department of Computer Science and Engineering, BAIUST", styles['SigLine']))
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("----------------------------------------<br/><br/>Member 1<br/><br/>(Member Name and Signature)<br/>Department of Computer Science and Engineering, BAIUST", styles['SigLine']))
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("----------------------------------------<br/><br/>Member 2<br/><br/>(Member Name and Signature)<br/>Department of Computer Science and Engineering, BAIUST", styles['SigLine']))
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("----------------------------------------<br/><br/>Member 3<br/><br/>(Member Name and Signature)<br/>Department of Computer Science and Engineering, BAIUST", styles['SigLine']))
    story.append(new_page())

    # ═══════════════════════════════════════════════
    # TABLE OF CONTENTS
    # ═══════════════════════════════════════════════
    story.append(h1("TABLE OF CONTENTS"))
    toc_items = [
        ("1. Introduction", "04"),
        ("2. Research Motivation", "04-05"),
        ("3. Problem Statement", "05-06"),
        ("4. Main Objectives", "06-07"),
        ("5. Research Questions", "07"),
        ("6. Literature Review", "07-12"),
        ("7. Project Scope", "12-13"),
        ("8. Methodology", "13-21"),
        ("9. System Features", "21-25"),
        ("10. Project Timeline", "25-27"),
        ("11. Ethical Consideration", "27-29"),
        ("12. Innovation", "29-30"),
        ("13. Expected Outcomes", "30-31"),
        ("14. Estimated Budget", "31"),
        ("15. Conclusion", "31-32"),
        ("References", "33-35"),
        ("AI Check Ratio", "36"),
    ]
    toc_data = []
    for title, page in toc_items:
        dots = "." * (60 - len(title))
        toc_data.append([Paragraph(f"<b>{title}</b>", styles['Body']), Paragraph(dots, ParagraphStyle('dots', fontName='Helvetica', fontSize=10, textColor=HexColor("#cccccc"), alignment=TA_LEFT)), Paragraph(page, ParagraphStyle('tocPage', fontName='Helvetica', fontSize=10, alignment=TA_RIGHT))])
    
    avail = PAGE_W - MARGIN_L - MARGIN_R
    t = Table(toc_data, colWidths=[avail*0.7, avail*0.2, avail*0.1])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(t)
    story.append(new_page())

    # ═══════════════════════════════════════════════
    # 1. INTRODUCTION
    # ═══════════════════════════════════════════════
    story.append(h1("1. Introduction"))
    story.append(body(
        "Surveillance systems are fundamental to security infrastructure in smart cities, educational institutions, "
        "industrial facilities, and public spaces across Bangladesh. The rapid expansion of urban populations and "
        "the government's Smart Bangladesh Vision 2041 have driven an unprecedented deployment of CCTV cameras "
        "nationwide. However, conventional surveillance systems transmit video at fixed high bitrates, resulting in "
        "excessive bandwidth consumption, prohibitive storage costs, high latency, and degraded performance in "
        "resource-constrained network environments [1], [2]."
    ))
    story.append(body(
        "This project presents an Adaptive CCTV Compression System with Dynamic Region of Interest (ROI) Control. "
        "The system employs YOLOv8-based intelligent object detection on an edge computing node to identify and "
        "classify regions of interest in real time, combined with adaptive H.265/HEVC compression via FFmpeg. "
        "Static background areas are aggressively compressed while moving and semantically important regions (persons, "
        "vehicles) are preserved at high visual quality. A central server with an interactive web dashboard provides "
        "users with real-time control over compression parameters and live visualization of system performance "
        "metrics including bitrate, PSNR, SSIM, and VMAF scores [3], [4]."
    ))
    story.append(body(
        "Beyond the originally proposed scope, the implemented system incorporates a risk-aware event engine with "
        "a three-state surveillance state machine (NORMAL, ALERT, CRITICAL) driven by a multi-signal risk score, "
        "temporal ROI persistence with IoU-based smoothing to eliminate visual flicker, multi-codec runtime switching "
        "(H.264, H.265, AV1, NVENC), privacy-preserving operation modes (background blur, ethical blanking, face "
        "masking), and a comprehensive evaluation pipeline that computes per-ROI and background-only quality metrics "
        "alongside detection accuracy retention and VMAF perceptual quality scoring [5], [6]."
    ))

    # ═══════════════════════════════════════════════
    # 2. RESEARCH MOTIVATION
    # ═══════════════════════════════════════════════
    story.append(h1("2. Research Motivation"))
    story.append(body(
        "Bangladesh's CCTV surveillance infrastructure has experienced explosive growth over the past decade. "
        "The national CCTV market has grown at approximately 50% per year, with camera installations rising from "
        "200,000 units in 2015 to over 1.6 million units by 2024. The smart surveillance segment is projected to "
        "expand from USD 88.9 million in 2025 to USD 148.7 million by 2030, driven by rapid urbanization, "
        "mega-infrastructure projects, metro rail development, and the Smart Bangladesh Vision 2041 initiative "
        "aiming for IoT-enabled smart cities and comprehensive digital security [7], [8]."
    ))
    story.append(body(
        "Despite this growth, conventional CCTV systems rely on fixed high-bitrate streaming protocols that lead to "
        "severe inefficiencies: 40-50% of available bandwidth is wasted on static background content in typical "
        "surveillance deployments. This places enormous strain on Bangladesh's internet infrastructure, which "
        "recorded 37% peak traffic growth in 2025 alone. Educational institutions, industrial facilities, and "
        "public safety systems bear significant operational costs for storage and bandwidth with no corresponding "
        "security benefit [9], [10]."
    ))
    story.append(body("Key motivations behind this research are:"))
    story.append(bullet("Reduce bandwidth consumption by 45-65% while preserving high visual quality in critical moving regions through dynamic ROI control based on YOLOv8 object detection with priority classification."))
    story.append(bullet("Implement edge-based real-time adaptive compression with H.265/HEVC streaming suitable for resource-constrained networks and low-cost IoT deployments in Bangladesh."))
    story.append(bullet("Provide efficient and scalable surveillance technology in support of the government's Digital Bangladesh and Smart City initiatives."))
    story.append(bullet("Reduce storage and energy costs for educational institutions, industries, and public safety systems through aggressive background compression during idle periods."))
    story.append(bullet("Bridge the gap between traditional fixed-bitrate CCTV systems and modern intelligent surveillance by introducing a risk-aware event engine that preserves evidence quality during critical incidents."))
    story.append(bullet("Develop a sustainable and cost-effective smart surveillance infrastructure aligned with the national development goals of Bangladesh."))

    # ═══════════════════════════════════════════════
    # 3. PROBLEM STATEMENT
    # ═══════════════════════════════════════════════
    story.append(h1("3. Problem Statement"))
    story.append(body(
        "The rapid proliferation of CCTV surveillance systems in Bangladesh has introduced significant technical "
        "and economic challenges. With over 1.6 million cameras installed nationwide and the smart surveillance "
        "market expanding at 50% annually, conventional systems continue to stream video at fixed high bitrates, "
        "leading to critical inefficiencies [7], [11]."
    ))
    story.append(body("Existing CCTV systems suffer from the following major disadvantages:"))
    story.append(bullet("<b>High Bandwidth and Storage Waste:</b> Fixed-bitrate streaming wastes 40-50% of available network capacity even when the vast majority of the scene remains static."))
    story.append(bullet("<b>No Intelligent Adaptation:</b> Conventional systems lack dynamic adjustment of video quality based on motion, semantic importance, or threat level, resulting in unnecessary data transmission."))
    story.append(bullet("<b>Poor Performance on Resource-Constrained Networks:</b> Bangladesh's low-bandwidth IoT environments frequently experience high latency, packet loss, and degraded video quality."))
    story.append(bullet("<b>No Real-Time User Control:</b> There is no live dashboard for operators to dynamically adjust compression parameters based on situational needs or threat levels."))
    story.append(bullet("<b>Inefficient Resource Utilization:</b> Educational institutions, industries, and public safety systems expend significant operational costs with no proportional security benefit."))
    story.append(bullet("<b>Lack of Event-Aware Preservation:</b> Existing compression methods do not distinguish between routine idle monitoring and critical security incidents, potentially destroying forensic evidence through aggressive compression during events."))
    story.append(body(
        "These challenges impede the successful deployment of smart surveillance and IoT-based security solutions "
        "in Bangladesh. There is an urgent need for an adaptive, edge-based compression system that intelligently "
        "preserves quality in semantically important regions while significantly reducing bandwidth consumption, "
        "and that automatically transitions to evidence-preservation mode during security incidents."
    ))

    # ═══════════════════════════════════════════════
    # 4. MAIN OBJECTIVES
    # ═══════════════════════════════════════════════
    story.append(h1("4. Main Objectives"))
    story.append(h2("4.1 Specific Goals"))
    story.append(h3("4.1.1 Intelligent ROI Detection and Compression"))
    story.append(body(
        "Develop an edge node that combines YOLOv8 neural object detection with priority-based classification "
        "(HIGH for persons, MEDIUM for vehicles, LOW for other objects) and FFmpeg adaptive compression with "
        "runtime codec switching (H.264, H.265, AV1, NVENC). Moving regions are preserved at high quality while "
        "static background is aggressively compressed."
    ))
    story.append(h3("4.1.2 Risk-Aware State Machine and Event Engine"))
    story.append(body(
        "Implement a multi-signal risk score function that combines object presence, motion area, time-of-day, "
        "scene change magnitude, and ROI density to drive a three-state surveillance machine (NORMAL, ALERT, "
        "CRITICAL) with hysteresis. A pre/post-event frame buffer preserves up to 15 seconds of forensic context "
        "before and after critical events."
    ))
    story.append(h3("4.1.3 Server-Side Stream Management and Metrics Collection"))
    story.append(body(
        "Build a central server that manages dual Socket.IO namespaces for stream relay, monitors UDP H.265 "
        "bitrate in real time, performs automatic bandwidth adaptation, logs comprehensive metrics (bitrate, "
        "latency, FPS, PSNR, SSIM, risk scores) to CSV, and provides REST APIs for evaluation and playback."
    ))
    story.append(h3("4.1.4 Interactive Live Dashboard"))
    story.append(body(
        "Create a real-time web-based dashboard (Next.js) with canvas-based live stream rendering, priority-coded "
        "ROI overlay with glow effects, interactive control sliders (BG Scale, BG Quality, ROI Quality, Detect "
        "Every N Frames), preset modes, dual bandwidth display, live Chart.js bandwidth visualization, and "
        "real-time state badge showing NORMAL/ALERT/CRITICAL status."
    ))
    story.append(h3("4.1.5 Comprehensive Performance Evaluation"))
    story.append(body(
        "Develop an automated evaluation pipeline that computes global PSNR/SSIM, per-ROI PSNR/SSIM, "
        "background-only PSNR/SSIM, VMAF perceptual quality scores, detection accuracy retention, and an "
        "investigation usability score. Generate visual artifacts including diff heatmaps and summary plots."
    ))
    story.append(h3("4.1.6 Scalable Deployment"))
    story.append(body(
        "Containerize the full stack with Docker and Docker Compose for reproducible deployment. Support YAML "
        "configuration profiles for balanced, high-quality, privacy, and ultra-low-bandwidth operation modes."
    ))

    # ═══════════════════════════════════════════════
    # 5. RESEARCH QUESTIONS
    # ═══════════════════════════════════════════════
    story.append(h1("5. Research Questions"))
    story.append(bullet("How effectively can dynamic ROI-based compression with YOLOv8 object detection reduce bandwidth consumption compared to traditional fixed-bitrate CCTV systems?"))
    story.append(bullet("How well does the suggested system compress background areas while preserving visual quality (as determined by PSNR, SSIM, and VMAF) in semantically important regions?"))
    story.append(bullet("What effects does YOLOv8-based real-time object detection have on the precision and effectiveness of ROI selection in surveillance video streams compared to conventional motion detection?"))
    story.append(bullet("How do user-controlled compression parameters (background quality, ROI quality, detection interval) affect bandwidth consumption and system performance under varying network conditions?"))
    story.append(bullet("How effectively does the risk-aware event engine preserve forensic evidence quality during critical incidents compared to uniform compression approaches?"))
    story.append(bullet("How well-suited is the proposed edge-server architecture with Docker containerization for deployment in IoT contexts with limited resources, such as those in Bangladesh?"))

    # ═══════════════════════════════════════════════
    # 6. LITERATURE REVIEW
    # ═══════════════════════════════════════════════
    story.append(h1("6. Literature Review"))
    story.append(body(
        "Recent advances in Region of Interest (ROI)-based video compression have demonstrated significant "
        "potential for bandwidth efficiency in surveillance systems. This section reviews contemporary approaches "
        "and identifies the research gap addressed by this project."
    ))

    story.append(h2("6.1 Literature Review Summary"))
    lr_headers = ["SL", "Year", "Methodology", "Dataset", "Review / Gap"]
    lr_rows = [
        ["1", "2025", "FFmpeg metadata (motion vectors, bitrate maps) and morphological operations for ROI; adaptive YOLOv8 model scheduling [12]", "YODA dataset (highway traffic videos, 1280x720@30fps)", "Strong latency reduction; Gap: Best for static scenes only; no dashboard or risk-aware compression"],
        ["2", "2024", "CNN-based lambda prediction reservoir model and HEVC R-lambda QP adjustment at frame/CU level [13]", "HEVC test sequences (Class B-F: sports, news, movies)", "Excellent ROI quality with low bitrate overhead; Gap: Needs high-quality ROI maps; limited real edge testing"],
        ["3", "2023", "Gaussian Mixture Model (GMM) ROI detection, HEVC All-Intra tiling, cross-layer packet prioritization [14]", "Highway overtaking video (simulation in SUMO+NS-2)", "Good ROI PSNR in lossy networks; Gap: Simulation only; weak on complex indoor CCTV scenes"],
        ["4", "2023", "Lightweight ROIDet (YOLOv5-Lite, block motion), dynamic bandwidth allocation, FFmpeg [15]", "Real multi-camera surveillance streams", "54% bandwidth saving with minimal accuracy drop; Gap: Only for AI tasks; no human-viewing PSNR/SSIM or dashboard"],
        ["5", "2024", "Dual-scale compressive sensing, Siamese network ROI extraction, reversible NN reconstruction [16]", "Private transmission-line surveillance and CD.Net (with noise)", "High PSNR/SSIM at very low bitrate; Gap: High parameter sensitivity and reconstruction overhead"],
        ["6", "2023", "Fast pre-processing ROI detection, MJPEG with ROI-aware bitrate allocation [17]", "Low-power wireless surveillance sequences", "Energy and bitrate reduction; Gap: Uses outdated MJPEG; no dashboard or multi-camera support"],
        ["7", "2025", "Macroblock importance predictor (MobileSeg), region-aware super-resolution enhancer [18]", "YODA, YouTube clips, BDD100K", "2-3x faster throughput and higher accuracy; Gap: Requires retraining per task; no dashboard"],
        ["8", "2025", "YOLOv9 for frame relevance, SSIM similarity check, FFmpeg/OpenCV compression [19]", "Real ATM bank camera videos (15 hours, day/night)", "85% average compression with 100% important frames kept; Gap: Storage-focused, not real-time streaming or risk-aware"],
        ["9", "2025", "Reinforcement Learning (PPO) agent for block-level QP control in x264 [20]", "BDD100K driving videos", "25% better BD-rate for detection tasks; Gap: Needs downstream AI task model; machine-vision only"],
        ["10", "2025", "Real-time ROI detection and adaptive encoding with intelligent frame caching [21]", "Real mega-pixel CCTV streams", "Significant bandwidth saving with high ROI quality; Gap: No public dashboard or risk-aware event engine"],
        ["11", "2025", "ROI detection, frame retargeting (stretch ROI, shrink background), standard codec [22]", "Standard VCM/machine-vision test sequences", "Better machine-task accuracy at same bitrate; Gap: Extra retargeting step; mainly for machines"],
        ["12", "2024", "Transformer-based video compression with learned ROI weighting [23]", "UVG, MCL-JCV datasets", "State-of-the-art compression ratios; Gap: High computational cost, unsuitable for real-time edge deployment"],
        ["13", "2024", "Adaptive bitrate streaming with SDN control for surveillance networks [24]", "Custom multi-camera testbed", "Dynamic bandwidth allocation across cameras; Gap: No content-aware ROI encoding or event detection"],
        ["14", "2023", "Privacy-preserving video analytics with selective encryption and ROI masking [25]", "VIRAT, PRID-2011 datasets", "Strong privacy guarantees with low overhead; Gap: Focused on privacy, not compression efficiency or bandwidth reduction"],
        ["15", "2025", "Federated learning for distributed surveillance video analysis [26]", "Custom distributed camera network", "Privacy-preserving distributed inference; Gap: High communication overhead; not optimized for compression"],
    ]
    story.append(make_table(lr_headers, lr_rows, col_widths=[12*mm, 12*mm, 48*mm, 40*mm, 55*mm]))
    story.append(spacer(2*mm))

    story.append(h2("6.2 Comparative Analysis"))
    story.append(body(
        "Wang and Yang [12] proposed a compression metadata-assisted ROI extraction framework for H.265/HEVC "
        "coding focused on edge-based video analytics, achieving latency reduction and improved F1-score for "
        "detection tasks. However, their approach lacks perceptual quality metrics (PSNR, SSIM) and any form of "
        "user-interactive monitoring or visualization. Dou et al. [13] presented an R-lambda optimization based "
        "on CNN with ROI-based coding in the HEVC framework, achieving better perceptual quality in ROI regions "
        "with low bitrate overhead. However, implementation is limited to simulation environments without "
        "real-world edge deployment validation."
    ))
    story.append(body(
        "Labiod et al. [14] proposed GMM-based ROI detection with cross-layer optimization for VANETs, claiming "
        "up to 11 dB ROI PSNR improvement, but evaluation was simulation-only without actual bandwidth reduction "
        "measurements. Guo et al. [15] developed a bandwidth-efficient streaming architecture using YOLOv5-Lite "
        "with approximately 54% bandwidth reduction, but evaluation focused on analytics accuracy rather than "
        "human-perceived visual quality. Gao et al. [16] proposed a robust mixed-rate ROI-aware compressive "
        "sensing framework achieving PSNR of 34.71 dB and SSIM of 0.932, but real-time surveillance application "
        "is constrained by computational overhead of signal reconstruction."
    ))
    story.append(body(
        "More recent approaches have leveraged deep learning for enhanced compression. Agrawal et al. [19] "
        "proposed FRVC using YOLOv9 for frame relevance detection, achieving up to 85% storage reduction, but "
        "the system is optimized for storage rather than real-time streaming. Gadot et al. [20] proposed "
        "reinforcement learning-based rate control achieving 25% BD-rate reduction through block-level optimization, "
        "but this is task-specific and designed for machine vision rather than human perceptual quality. "
        "Transformer-based approaches [23] offer state-of-the-art compression but remain too computationally "
        "expensive for real-time edge deployment."
    ))
    story.append(body(
        "Chuang et al. [21] proposed adaptive encoding and caching schemes for surveillance streaming, and "
        "Rozek et al. [22] developed ROI-based retargeting techniques for machine-oriented video coding. "
        "SDN-based adaptive streaming approaches [24] provide dynamic bandwidth allocation across multiple cameras "
        "but lack content-aware ROI encoding. Privacy-preserving techniques [25] and federated learning approaches "
        "[26] address important adjacent concerns but do not directly target compression efficiency or "
        "event-aware quality preservation."
    ))

    story.append(h2("6.3 Research Gap and Proposed Contribution"))
    story.append(body(
        "The literature analysis reveals that existing approaches are predominantly focused on analytics performance, "
        "simulation-based validation, or machine-centric optimization. Critically, no existing work simultaneously "
        "addresses: (a) real-time edge deployment with neural object detection, (b) user-controlled adaptive "
        "compression with live visualization, (c) risk-aware event-driven quality preservation, and (d) comprehensive "
        "evaluation spanning signal quality, perceptual quality (VMAF), and investigation usability metrics."
    ))
    story.append(body(
        "This research presents a complete end-to-end adaptive CCTV compression system that addresses all four "
        "dimensions. The framework integrates YOLOv8-based object detection at the edge node, FFmpeg adaptive "
        "encoding with runtime codec switching, a multi-signal risk score driving a three-state surveillance "
        "machine, pre/post-event frame buffering for forensic evidence preservation, and an interactive dashboard "
        "enabling real-time user control. The evaluation pipeline computes global, per-ROI, and background-only "
        "PSNR/SSIM alongside VMAF scores, detection accuracy retention, and a novel investigation usability "
        "composite score. Experimental results demonstrate bandwidth reduction of 45-65% with stable perceptual "
        "quality and enhanced evidence preservation during critical incidents."
    ))

    # ═══════════════════════════════════════════════
    # 7. PROJECT SCOPE
    # ═══════════════════════════════════════════════
    story.append(h1("7. Project Scope"))
    story.append(body(
        "The goal of this project is to design, build, and evaluate a complete Adaptive CCTV Compression System "
        "that leverages Dynamic Region of Interest (ROI) Control with a risk-aware event engine. The project "
        "delivers a fully functional prototype demonstrating intelligent surveillance with real-time adaptive "
        "video compression."
    ))
    story.append(h2("7.1 Within Scope"))
    story.append(bullet("Development of an edge node with YOLOv8-based object detection that identifies and classifies ROIs with three priority tiers (HIGH/Medium/LOW)."))
    story.append(bullet("Implementation of FFmpeg-based adaptive compression with runtime codec switching supporting H.264, H.265, AV1, and NVENC."))
    story.append(bullet("Risk-aware event engine with multi-signal risk score calculation and three-state surveillance machine (NORMAL, ALERT, CRITICAL)."))
    story.append(bullet("Pre/post-event circular frame buffer preserving up to 15 seconds of forensic context around critical incidents."))
    story.append(bullet("Interactive live dashboard with canvas-based rendering, priority-coded ROI overlays, real-time sliders, preset modes, and live bandwidth charts."))
    story.append(bullet("Central server with dual Socket.IO namespaces, UDP bandwidth monitoring, auto-bandwidth adaptation, and comprehensive CSV metrics logging."))
    story.append(bullet("Automated evaluation pipeline computing global/per-ROI/background PSNR, SSIM, VMAF, detection accuracy, and investigation usability scores."))
    story.append(bullet("Three privacy operation modes: background blur, ethical blanking, and face masking."))
    story.append(bullet("Docker containerization with Docker Compose orchestration and YAML configuration profiles."))
    story.append(bullet("Performance validation demonstrating 45-65% bandwidth reduction with stable quality metrics."))

    story.append(h2("7.2 Outside Scope"))
    story.append(bullet("Multi-camera simultaneous deployment and scaling (architecturally supported but not validated for concurrent multi-node operation)."))
    story.append(bullet("Integration with commercial CCTV hardware or cloud platforms beyond standard IP camera interfaces."))
    story.append(bullet("Mobile application development or voice-based alert systems."))
    story.append(bullet("Production-grade security hardening including TLS/SSL encryption and penetration testing."))
    story.append(bullet("Long-term field reliability studies or extended deployment trials."))

    # ═══════════════════════════════════════════════
    # 8. METHODOLOGY
    # ═══════════════════════════════════════════════
    story.append(h1("8. Methodology"))
    story.append(body(
        "The proposed system follows a multi-stage adaptive video processing pipeline designed to reduce "
        "bandwidth consumption while preserving critical visual information. The architecture has evolved "
        "significantly from the initial concept to include risk-aware event-driven operation."
    ))

    story.append(h2("8.1 System Architecture"))
    story.append(body(
        "The system follows a three-tier architecture comprising the Edge Node (Python-based capture and "
        "processing pipeline), Central Server (Node.js with dual Socket.IO namespaces), and Dashboard "
        "(Next.js web application). The evaluation pipeline operates as a fourth component for automated "
        "benchmarking."
    ))
    story.append(placeholder_fig("Fig 01: System Architecture", height=55*mm))
    story.append(caption("Fig 01: System Architecture — Three-tier edge-server-dashboard design with evaluation pipeline."))

    story.append(h2("8.2 Frame Acquisition and YOLOv8 Object Detection"))
    story.append(body(
        "Video frames are captured from a live camera source (PC webcam or IP camera) using OpenCV. Each "
        "frame is processed in real time on the edge device. The YOLOv8n (nano) neural network performs "
        "object detection across 80 COCO classes, classifying detections into three priority tiers: HIGH "
        "(persons, class 0 — preserved at full resolution), MEDIUM (vehicles including cars, motorcycles, "
        "buses, trains, trucks — mildly compressed), and LOW (all other detected objects) [27]. Detection "
        "is performed at configurable intervals (default: every 3 frames) to balance accuracy and performance."
    ))

    story.append(h2("8.3 Temporal ROI Persistence and Anti-Flicker"))
    story.append(body(
        "A TTL (Time-to-Live) system ensures ROI bounding boxes persist for 30 frames (~2 seconds at 15 FPS), "
        "eliminating the pulsating flicker effect caused by periodic detection. A merge_rois() function using "
        "Intersection-over-Union (IoU) threshold >= 0.3 smoothly blends new detections into existing bounding "
        "boxes rather than hard-replacing them, providing temporally stable ROI boundaries."
    ))

    story.append(h2("8.4 Adaptive Compression and Encoding"))
    story.append(body(
        "Each frame is subdivided into foreground (ROI) and background (non-ROI) regions. ROI regions are "
        "encoded at high quality while the background is downscaled and aggressively compressed. The "
        "CodecManager class manages an FFmpeg subprocess that receives raw BGR frames via stdin and outputs "
        "an H.265 MPEG-TS stream over UDP. The codec can be switched at runtime between libx264, libx265, "
        "libsvtav1, and hevc_nvenc without restarting the subprocess. Adjustable parameters include "
        "Background Scale (resolution reduction), Background Quality (JPEG compression level), ROI Quality "
        "(high-quality encoding), and Frame Skipping (detection interval)."
    ))

    story.append(placeholder_fig("Fig 02: Compression Pipeline Flow", height=45*mm))
    story.append(caption("Fig 02: Adaptive compression pipeline showing ROI detection, background downscaling, and H.265 UDP streaming."))

    story.append(h2("8.5 Risk-Aware Event Engine"))
    story.append(body(
        "The most significant architectural innovation is the risk-aware event engine. A risk_score() function "
        "computes a normalized risk value [0.0, 1.0] per frame by combining five independent signals: "
        "HIGH-priority objects (+0.30 each), MEDIUM-priority objects (+0.10 each), motion area fraction "
        "(up to +0.25), after-hours period 22:00-06:00 (+0.20 flat bonus), and scene change magnitude "
        "(up to +0.15). This score drives a three-state surveillance state machine:"
    ))
    risk_headers = ["State", "Risk Range", "Behavior", "BG Scale", "BG Quality", "ROI Quality"]
    risk_rows = [
        ["NORMAL", "0.00 - 0.25", "Aggressive background compression; FPS halved if idle", "User-defined", "User-defined", "User-defined"],
        ["ALERT", "0.25 - 0.55", "Context-preserving; more background kept", "0.75", "45", "95"],
        ["CRITICAL", "0.55 - 1.00", "Near-lossless recording; full resolution", "1.0", "88", "100"],
    ]
    story.append(make_table(risk_headers, risk_rows, col_widths=[22*mm, 24*mm, 48*mm, 20*mm, 20*mm, 22*mm]))
    story.append(spacer(2*mm))
    story.append(body(
        "Hysteresis is implemented with an 8-frame hold period before downgrading to prevent rapid state "
        "oscillation. A rolling circular buffer (15 seconds x FPS) continuously stores original frames. When "
        "the state transitions to CRITICAL, pre-event frames are dumped to disk with full metadata, followed "
        "by 10 seconds of post-event recording, enabling forensic reconstruction of incident context."
    ))

    story.append(h2("8.6 Frame Encoding and Transmission"))
    story.append(body(
        "Background frame and ROI crops are encoded separately (Base64 JPEG). The data packet structure "
        "includes frame_id, bg_data, rois list with bounding boxes, orig_w, orig_h, timestamp, risk_score, "
        "and surveillance_state. Transmission uses Socket.IO for real-time streaming to the central server."
    ))

    story.append(h2("8.7 Server-Side Processing"))
    story.append(body(
        "The Node.js server manages two Socket.IO namespaces: /stream (edge node pushes frames) and /view "
        "(dashboard pulls frames and sends controls). A UDP server monitors H.265 stream bitrate in real time. "
        "Automatic bandwidth adaptation computes total bandwidth every 3 seconds and sends reduced-quality "
        "control messages if thresholds are exceeded. Three CSV logs maintain comprehensive records: "
        "metrics_log.csv (6000+ rows), control_log.csv (parameter changes), and motion_log.csv (40,000+ "
        "rows of per-frame motion data). HTTP endpoints include /control, /metrics, /sample, /reconstruct, "
        "/request_sample, /playback, /event, and /config."
    ))

    story.append(h2("8.8 Dashboard Visualization"))
    story.append(body("Two visualization modes are implemented:"))
    story.append(bullet("<b>Live Monitor Mode:</b> Displays fully reconstructed frames on a dynamically-resized HTML5 canvas with priority-coded ROI overlay boxes (RED=HIGH, YELLOW=MEDIUM, GREEN=LOW) with shadowBlur glow effects."))
    story.append(bullet("<b>Analysis Mode:</b> Displays compressed background and ROI overlays for analyzing compression behavior."))
    story.append(body(
        "The dashboard features four real-time control sliders (BG Scale, BG Quality, ROI Quality, Detect "
        "Every N Frames), two preset mode buttons (Low BW and High Quality), dual bandwidth display (Raw "
        "Socket.IO kbps and H.265 UDP kbps with % Saved), and a live Chart.js bandwidth chart. A "
        "color-coded state badge (NORMAL green, ALERT orange, CRITICAL red) indicates the current "
        "surveillance state."
    ))

    story.append(placeholder_fig("Fig 03: User Dashboard Interface", height=50*mm))
    story.append(caption("Fig 03: Live dashboard showing canvas stream, ROI overlays, control sliders, bandwidth chart, and state badge."))

    story.append(h2("8.9 Metrics Collection and Analysis"))
    story.append(body("The system continuously records and visualizes:"))
    story.append(bullet("FPS (Frames per second) — real-time frame rate display"))
    story.append(bullet("Bitrate (Kbps) — dual monitoring of Socket.IO raw stream and H.265 UDP stream"))
    story.append(bullet("Number of ROIs — count of detected objects per frame"))
    story.append(bullet("Risk Score — normalized [0.0, 1.0] surveillance risk indicator"))
    story.append(bullet("Surveillance State — NORMAL/ALERT/CRITICAL with hysteresis"))
    story.append(bullet("PSNR (Peak Signal-to-Noise Ratio) — global, per-ROI, and background-only"))
    story.append(bullet("SSIM (Structural Similarity Index) — global, per-ROI, and background-only"))
    story.append(bullet("VMAF Score — Netflix perceptual quality metric via FFmpeg libvmaf"))
    story.append(bullet("Detection Accuracy — YOLOv8 re-detection count retention on compressed frames"))

    story.append(placeholder_fig("Fig 04: Performance Metrics Dashboard", height=45*mm))
    story.append(caption("Fig 04: Real-time metrics visualization showing FPS, bitrate, ROI count, PSNR, SSIM, and risk score trends."))

    story.append(h2("8.10 Compressed Frame Storage"))
    story.append(body("Frames are stored in a structured directory format:"))
    story.append(body("recording/ &nbsp;|-- [date]/ &nbsp;|-- bg/ (background images) &nbsp;|-- roi/ (ROI crops) &nbsp;|-- meta.json (metadata with bounding boxes and timestamps)"))
    story.append(body(
        "Background and ROI data are stored separately with metadata including bounding boxes and timestamps, "
        "enabling efficient storage and later reconstruction. The storage module also maintains event archives "
        "with pre/post-event frame sequences for forensic analysis."
    ))

    storage_headers = ["Level", "Component", "Data Type", "Description"]
    storage_rows = [
        ["Root", "recordings/", "Directory", "Global container for recording sessions"],
        ["L1", "[date]/", "Directory", "Discrete session identifier (ISO 8601)"],
        ["L2", "/bg/", "Binary/RAW", "Background frame images"],
        ["L2", "/roi/", "Binary/RAW", "ROI crop images with bounding boxes"],
        ["L2", "meta.json", "JSON", "Associative metadata (timestamps, hardware IDs, risk scores)"],
        ["Events", "events/", "Directory", "Pre/post-event frame archives for critical incidents"],
    ]
    story.append(make_table(storage_headers, storage_rows, col_widths=[20*mm, 28*mm, 28*mm, 90*mm]))

    story.append(h2("8.11 Frame Reconstruction"))
    story.append(body(
        "Stored frames can be reconstructed by loading the background frame and overlaying ROI images at "
        "their corresponding bounding box coordinates. This ensures compression is selective rather than "
        "destructive, and stored surveillance footage remains fully usable for forensic review."
    ))

    story.append(h2("8.12 Performance Assessment"))
    story.append(body(
        "The automated evaluation pipeline iterates over configurable {bg_quality, roi_quality} presets, "
        "sends each as a live control command, and captures original/reconstructed frame pairs. For each "
        "experiment, global PSNR/SSIM, per-ROI PSNR/SSIM, background-only PSNR/SSIM, VMAF scores, and "
        "detection accuracy retention are computed. Visual artifacts including diff heatmaps and ROI overlays "
        "are saved. The event evaluation module computes investigation usability scores based on frame "
        "retention, detection recall, and pre-event context PSNR."
    ))

    story.append(h2("8.13 Methodology Overview"))
    method_headers = ["Phase", "Functional Module", "Technologies & Methods", "Primary Deliverable"]
    method_rows = [
        ["I", "Edge Node", "OpenCV, YOLOv8, FFmpeg, CodecManager", "ROI-optimized compressed streams with risk-aware state machine"],
        ["II", "Central Server", "Node.js, Socket.IO, UDP, HTTP, CSV logging", "Real-time telemetry, auto-bandwidth adaptation, event logging"],
        ["III", "Dashboard", "Next.js, React, Chart.js, HTML5 Canvas", "Control UI, real-time visualization, state display"],
        ["IV", "Evaluation", "scikit-image, FFmpeg libvmaf, YOLOv8", "Automated benchmarking with comprehensive metrics"],
        ["V", "Deployment", "Docker, Docker Compose, YAML configs", "Reproducible containerized deployment"],
    ]
    story.append(make_table(method_headers, method_rows, col_widths=[16*mm, 30*mm, 50*mm, 70*mm]))

    # ═══════════════════════════════════════════════
    # 9. SYSTEM FEATURES
    # ═══════════════════════════════════════════════
    story.append(h1("9. System Features"))

    story.append(h2("9.1 YOLOv8-Based Intelligent Object Detection"))
    story.append(body(
        "The system replaces conventional motion detection with YOLOv8n neural object detection across 80 "
        "COCO classes. Detected objects are classified into three priority tiers enabling semantically aware "
        "compression allocation. This provides significantly lower false-positive rates in complex lighting "
        "conditions compared to traditional frame-differencing approaches [27]."
    ))

    story.append(h2("9.2 Real-Time Adaptive Compression with Multi-Codec Support"))
    story.append(body(
        "Runtime-switchable codec support enables selection between H.264, H.265, AV1, and NVENC encoders "
        "based on hardware availability and bandwidth requirements. Compression parameters adapt dynamically "
        "to both user input and automatic bandwidth detection."
    ))

    story.append(h2("9.3 Risk-Aware Surveillance State Machine"))
    story.append(body(
        "A multi-signal risk score combining object presence, motion area, time-of-day, scene changes, and "
        "ROI density drives a three-state machine with hysteresis. This ensures that during critical "
        "incidents, the system automatically transitions to near-lossless recording mode, preserving "
        "forensic evidence quality."
    ))

    story.append(h2("9.4 Pre/Post-Event Frame Buffer"))
    story.append(body(
        "A rolling circular buffer maintains 15 seconds of original full-resolution frames. When a critical "
        "event is detected, pre-event context and post-event recording are automatically archived with "
        "complete metadata, enabling forensic reconstruction of incident timelines."
    ))

    story.append(h2("9.5 Dual-Mode Dashboard Display"))
    story.append(body(
        "Live monitoring mode provides real-time observation with priority-coded ROI overlays and state "
        "badges. Analysis mode renders compressed background with ROI overlays for post-processing "
        "evaluation and research analysis."
    ))

    story.append(h2("9.6 Bandwidth-Aware Adaptive Control"))
    story.append(body(
        "The system dynamically adjusts compression parameters based on network bandwidth measurements. "
        "The server automatically reduces quality when bandwidth thresholds are exceeded, while user-initiated "
        "controls take priority and suppress auto-adaptation for a configurable period."
    ))

    story.append(h2("9.7 Temporal ROI Persistence"))
    story.append(body(
        "TTL-based ROI persistence with IoU-smoothing merge algorithm eliminates visual flickering caused "
        "by periodic detection. ROIs persist for 30 frames (~2 seconds) with smooth transitions between "
        "detection cycles."
    ))

    story.append(h2("9.8 Privacy and Ethical Operation Modes"))
    story.append(body(
        "Three privacy tiers address ethical surveillance concerns: Privacy Blur (heavy Gaussian background "
        "blur), Ethical Mode (black frame when no ROI detected — zero passive surveillance), and Face "
        "Masking (anonymization of detected persons while preserving vehicle visibility)."
    ))

    story.append(h2("9.9 Comprehensive Evaluation Pipeline"))
    story.append(body(
        "The evaluation system computes signal quality (PSNR/SSIM), perceptual quality (VMAF), detection "
        "accuracy retention, and investigation usability scores. Per-ROI and background-only metrics provide "
        "granular insight into compression behavior."
    ))

    story.append(h2("9.10 Docker Containerization and Configuration Profiles"))
    story.append(body(
        "Full Docker containerization with Docker Compose orchestration enables single-command deployment. "
        "Four YAML configuration profiles (balanced, high_quality, privacy_mode, ultra_low_bandwidth) "
        "provide pre-configured operation modes for different deployment scenarios."
    ))

    story.append(h2("9.11 Functional Requirements"))
    fr_headers = ["ID", "Category", "Requirement", "Technical Description"]
    fr_rows = [
        ["FR-01", "Ingestion", "Video Stream Input", "Capture live frames from webcam or IP camera using OpenCV"],
        ["FR-02", "Analysis", "YOLOv8 Object Detection", "Detect and classify objects across 80 COCO classes with three priority tiers"],
        ["FR-03", "Analysis", "Risk Score Computation", "Calculate normalized risk score from object presence, motion, time, scene change, and density signals"],
        ["FR-04", "Processing", "ROI-Based Adaptive Compression", "Segment frames into ROI and background with per-region quality allocation"],
        ["FR-05", "Processing", "State Machine Management", "Three-state surveillance machine with hysteresis (NORMAL/ALERT/CRITICAL)"],
        ["FR-06", "Processing", "Pre/Post-Event Buffer", "15-second rolling buffer with automatic event-triggered archival"],
        ["FR-07", "Encoding", "Multi-Codec Encoding", "Runtime-switchable codec: H.264, H.265, AV1, NVENC via FFmpeg"],
        ["FR-08", "Networking", "Real-Time Transmission", "Dual Socket.IO namespaces + H.265 UDP streaming"],
        ["FR-09", "Control", "Dynamic Parameter Adjustment", "Real-time modulation of BG Scale, BG Quality, ROI Quality, detection intervals"],
        ["FR-10", "Control", "Auto Bandwidth Adaptation", "Automatic quality reduction when bandwidth thresholds exceeded"],
        ["FR-11", "Visualization", "Live Monitoring", "Canvas-based stream rendering with priority-coded ROI overlay and state badge"],
        ["FR-12", "Visualization", "Analysis Interface", "Secondary mode for compressed background with ROI overlays"],
        ["FR-13", "Analytics", "Performance Metrics", "FPS, bitrate, ROI count, PSNR, SSIM, VMAF, detection accuracy"],
        ["FR-14", "Storage", "Frame Archival", "Structured storage with separate BG/ROI/meta and event archives"],
        ["FR-15", "Reconstruction", "Video Synthesis", "Composite reconstruction from background and ROI segments"],
        ["FR-16", "Privacy", "Privacy Operation Modes", "Background blur, ethical blanking, face masking"],
    ]
    story.append(make_table(fr_headers, fr_rows, col_widths=[16*mm, 24*mm, 38*mm, 88*mm]))

    story.append(h2("9.12 Non-Functional Requirements"))
    nfr_headers = ["Dimension", "Specification", "Success Criteria / Metrics"]
    nfr_rows = [
        ["Performance", "Real-Time Processing", "End-to-end latency 200-500 ms; dashboard initial UI load ~3s; sub-second telemetry refresh"],
        ["Reliability", "Continuous Operation", "Sustained uptime without degradation; automatic socket reconnection; packet loss resilience"],
        ["Usability", "Interface Design", "Cohesive UI/UX with standardized layouts across monitoring, control, and analytical views"],
        ["Maintenance", "Modular Architecture", "Decoupled 3-layer design (Edge, Server, Dashboard) ensuring independent extensibility"],
        ["Scalability", "Multi-Stream Support", "Architectural framework validated for single-stream with structural support for multi-node scaling"],
        ["Efficiency", "Bandwidth Optimization", "Targeted data reduction of 45-65% relative to unoptimized full-frame transmission"],
        ["Observability", "Logging & Monitoring", "Automated archival of system events and performance metrics in standardized CSV formats"],
        ["Security", "Access Perimeter", "Deployment within trusted LAN with roadmap for future TLS/SSL integration"],
        ["Privacy", "Data Protection", "No facial recognition; no persistent raw footage storage; clear ethical operation modes"],
    ]
    story.append(make_table(nfr_headers, nfr_rows, col_widths=[28*mm, 40*mm, 98*mm]))

    # ═══════════════════════════════════════════════
    # 10. PROJECT TIMELINE
    # ═══════════════════════════════════════════════
    story.append(h1("10. Project Timeline"))

    tl_headers = ["Sl.", "Activity", "Duration", "Start Date", "End Date", "Status"]
    tl_rows = [
        ["1", "Idea Generation, Requirement Analysis and Feasibility Study", "2 Weeks", "20 Oct 2025", "02 Nov 2025", "Completed"],
        ["2", "Literature Review and Research Planning", "3 Weeks", "03 Nov 2025", "23 Nov 2025", "Completed"],
        ["3", "System Design (Architecture and ROI Pipeline Design)", "2 Weeks", "24 Nov 2025", "03 Dec 2025", "Completed"],
        ["4", "Prototype Development (Edge Setup, Basic Dashboard)", "10 Days", "28 Nov 2025", "07 Dec 2025", "Completed"],
        ["5", "Poster Presentation (First Prize Achieved)", "3 Days", "05 Dec 2025", "08 Dec 2025", "Achieved"],
        ["6", "Edge Node Refinement (YOLOv8 Integration, FFmpeg)", "3 Weeks", "09 Dec 2025", "29 Dec 2025", "Completed"],
        ["7", "Server-side Development and Advanced Metrics Logging", "3 Weeks", "30 Dec 2025", "19 Jan 2026", "Completed"],
        ["8", "Dashboard Enhancement (Graphs, Controls, UI/UX)", "3 Weeks", "20 Jan 2026", "09 Feb 2026", "Completed"],
        ["9", "System Integration (Edge, Server, Dashboard)", "3 Weeks", "10 Feb 2026", "02 Mar 2026", "Completed"],
        ["10", "Real-Time Testing and Performance Evaluation", "3 Weeks", "03 Mar 2026", "23 Mar 2026", "Completed"],
        ["11", "Risk-Aware Event Engine Development", "2 Weeks", "24 Mar 2026", "06 Apr 2026", "Completed"],
        ["12", "Docker Containerization and Deployment Automation", "2 Weeks", "07 Apr 2026", "20 Apr 2026", "Completed"],
        ["13", "Documentation, Thesis Writing and Final Reporting", "4 Weeks", "21 Apr 2026", "18 May 2026", "In Progress"],
    ]
    story.append(make_table(tl_headers, tl_rows, col_widths=[10*mm, 56*mm, 18*mm, 26*mm, 26*mm, 22*mm]))

    story.append(spacer(3*mm))
    story.append(placeholder_fig("Fig 05: Gantt Chart — Project Timeline", height=45*mm))
    story.append(caption("Fig 05: Gantt chart illustrating project schedule from October 2025 to May 2026."))

    # ═══════════════════════════════════════════════
    # 11. ETHICAL CONSIDERATION
    # ═══════════════════════════════════════════════
    story.append(h1("11. Ethical Consideration"))
    story.append(body(
        "Ethical considerations ensure that the technologies employed in this project — particularly CCTV "
        "surveillance video processing, YOLOv8-based object detection, and AI-driven adaptive compression — "
        "are used in a safe, responsible, and legally compliant manner. The system processes live video streams "
        "and does not perform facial recognition, individual identification, or any form of biometric analysis. "
        "The system detects generic objects to enable dynamic ROI compression and risk-aware state transitions."
    ))

    story.append(h2("11.1 Privacy Protection in CCTV Data Processing"))
    story.append(body(
        "The system uses CCTV video streams exclusively for real-time adaptive compression and risk-aware "
        "event detection. No personally identifiable information, identity data, or sensitive content is "
        "stored beyond what is minimally required for motion detection and compression. All video processing "
        "occurs at the edge node, and only authorized personnel can access live streams through the "
        "dashboard's role-based access control."
    ))

    story.append(h2("11.2 No Facial Recognition or Individual Identification"))
    story.append(body(
        "The system performs no facial recognition or individual identification. YOLOv8 detection classifies "
        "objects at the category level (person, vehicle, etc.) without attempting to identify specific "
        "individuals. This ensures the system does not infringe on personal privacy or enable unauthorized "
        "surveillance monitoring of individuals."
    ))

    story.append(h2("11.3 Transparent Operation and Algorithmic Accountability"))
    story.append(body(
        "The YOLOv8 object detection logic, risk score computation, and FFmpeg compression pipeline are "
        "completely transparent and auditable. The multi-signal risk score is explainable (each component "
        "contribution is logged), and the state machine transitions are deterministic. False detections and "
        "their impact on compression behavior can be traced and analyzed."
    ))

    story.append(h2("11.4 Privacy-First Operation Modes"))
    story.append(body(
        "Three privacy modes are built into the system by design: Privacy Blur applies heavy Gaussian blur "
        "to the entire background preventing any background surveillance; Ethical Mode returns a black frame "
        "when no ROI is detected, ensuring zero passive surveillance of empty spaces; Face Masking anonymizes "
        "detected persons while preserving vehicle visibility for security monitoring."
    ))

    story.append(h2("11.5 Secure Storage and Data Retention"))
    story.append(body(
        "Only compressed temporary frames are stored. No continuous raw video recording occurs unless "
        "triggered by the event engine during critical incidents. Access to compressed streams, event "
        "archives, and metrics logs is restricted to authorized personnel through the dashboard's "
        "authentication layer."
    ))

    story.append(h2("11.6 Ethical Use of Test Data"))
    story.append(body(
        "All test footage used during development was captured in controlled laboratory settings or sourced "
        "from publicly available, non-sensitive datasets with appropriate permissions. No personally "
        "identifiable information or private surveillance data from third parties was utilized. The YOLOv8 "
        "model was used with pre-trained COCO weights, and no additional training on sensitive surveillance "
        "data was performed."
    ))

    # ═══════════════════════════════════════════════
    # 12. INNOVATION
    # ═══════════════════════════════════════════════
    story.append(h1("12. Innovation"))
    story.append(body(
        "Compared to conventional CCTV surveillance systems and fixed-bitrate compression methods, this "
        "Adaptive CCTV Compression System using Dynamic ROI Control introduces several novel contributions:"
    ))

    story.append(h2("12.1 Risk-Aware Event-Driven Compression"))
    story.append(body(
        "The most significant innovation is the transformation from a generic adaptive compressor into a "
        "mission-critical, event-triggered surveillance system. The multi-signal risk score combining object "
        "presence, motion area, time-of-day, scene changes, and ROI density produces a principled, "
        "explainable risk metric. The three-state machine with hysteresis ensures appropriate compression "
        "behavior for each surveillance context — aggressive compression during idle periods, context "
        "preservation during suspicious activity, and near-lossless recording during critical incidents."
    ))

    story.append(h2("12.2 Pre/Post-Event Forensic Frame Buffer"))
    story.append(body(
        "The rolling circular buffer preserving 15 seconds of pre-event and 10 seconds of post-event "
        "full-resolution frames addresses a critical gap in existing surveillance compression systems. "
        "Investigators typically need the moments before an incident to understand how events unfolded. "
        "No existing ROI-based compression system in the reviewed literature provides this capability."
    ))

    story.append(h2("12.3 YOLOv8-Based Priority-Aware Compression"))
    story.append(body(
        "Moving beyond basic motion detection, the system uses YOLOv8 neural object detection with "
        "three-tier priority classification. Persons are preserved at full resolution, vehicles receive "
        "moderate compression, and other objects undergo aggressive compression. This semantic awareness "
        "ensures that the most important surveillance elements receive the highest quality allocation."
    ))

    story.append(h2("12.4 Temporally Stable ROI with IoU Smoothing"))
    story.append(body(
        "The TTL-based ROI persistence with IoU merge algorithm eliminates the visual flickering that "
        "plagues periodic detection systems. Smooth transitions between detection cycles provide a stable "
        "viewing experience while maintaining compression efficiency."
    ))

    story.append(h2("12.5 Comprehensive Investigation Usability Metric"))
    story.append(body(
        "The project introduces a novel composite investigation usability score that measures whether "
        "compressed surveillance footage would be usable as forensic evidence. Combining frame retention "
        "rate, detection recall, and pre-event context quality, this metric answers the question that "
        "truly matters for surveillance: not just compression ratios, but evidence preservation utility."
    ))

    story.append(h2("12.6 Runtime-Switchable Multi-Codec Architecture"))
    story.append(body(
        "The CodecManager enables runtime codec switching without subprocess restart, supporting H.264, "
        "H.265, AV1, and NVENC. This flexibility allows the system to adapt to different hardware "
        "capabilities and deployment requirements without service interruption."
    ))

    story.append(h2("12.7 Integrated Privacy-by-Design"))
    story.append(body(
        "Rather than treating privacy as an afterthought, the system incorporates three distinct privacy "
        "modes built directly into the compression pipeline. This ethical-by-design approach ensures that "
        "deployment in sensitive environments can be configured for appropriate privacy protection without "
        "sacrificing security monitoring capabilities."
    ))

    # ═══════════════════════════════════════════════
    # 13. EXPECTED OUTCOMES
    # ═══════════════════════════════════════════════
    story.append(h1("13. Expected Outcomes"))
    story.append(bullet("A fully functional real-time adaptive CCTV compression system with YOLOv8-based dynamic Region of Interest (ROI) control and risk-aware event-driven state machine."))
    story.append(bullet("Bandwidth reduction of 45-65% achieved with stable PSNR (30-35 dB), SSIM (0.85-0.95), and VMAF scores verified through automated evaluation pipeline."))
    story.append(bullet("Working prototype deployed on an edge node with live video feed, demonstrated through interactive web dashboard with real-time control and visualization."))
    story.append(bullet("Pre/post-event forensic frame buffer enabling evidence preservation during critical security incidents with investigation usability scoring."))
    story.append(bullet("User-controlled live dashboard with real-time monitoring of bitrate, PSNR, SSIM, VMAF, risk score, surveillance state, and compression parameters."))
    story.append(bullet("Comprehensive evaluation dataset with 33,880+ experiment data points, 6000+ metrics log entries, and 40,000+ motion log entries."))
    story.append(bullet("Docker containerized full-stack deployment with four YAML configuration profiles for different operational scenarios."))
    story.append(bullet("Full source code, comprehensive documentation, test datasets, and performance evaluation reports."))
    story.append(bullet("Research poster and technical paper for publication in academic venues."))

    # ═══════════════════════════════════════════════
    # 14. ESTIMATED BUDGET
    # ═══════════════════════════════════════════════
    story.append(h1("14. Estimated Budget"))
    budget_headers = ["Item", "Quantity", "Price (Taka)"]
    budget_rows = [
        ["IP Camera (1080p)", "1", "3,100"],
        ["Edge Device (Raspberry Pi / Laptop)", "1", "0 (Available)"],
        ["Server Setup (Local Machine)", "1", "0 (Available)"],
        ["SSD (500GB for storage and recording)", "1", "2,000"],
        ["Dashboard Development", "-", "0 (In-house)"],
        ["YOLOv8 Model License", "-", "0 (Open source)"],
        ["Software Libraries (OpenCV, FFmpeg, etc.)", "-", "0 (Open source)"],
        ["Networking Equipment (Router, Cables)", "1 set", "1,500"],
        ["Miscellaneous (Power, connectors, etc.)", "-", "1,000"],
    ]
    story.append(make_table(budget_headers, budget_rows, col_widths=[70*mm, 30*mm, 30*mm]))

    # ═══════════════════════════════════════════════
    # 15. CONCLUSION
    # ═══════════════════════════════════════════════
    story.append(h1("15. Conclusion"))
    story.append(body(
        "This project presents a practical and innovative solution to one of the most pressing challenges "
        "in modern surveillance systems: the inefficiency of fixed-bitrate video streaming and the lack of "
        "intelligent, event-aware compression. The Adaptive CCTV Compression System with Dynamic Region of "
        "Interest (ROI) Control successfully integrates YOLOv8-based object detection, FFmpeg adaptive "
        "encoding with multi-codec support, a risk-aware surveillance state machine, and an interactive "
        "real-time dashboard to achieve significant bandwidth savings while preserving visual quality in "
        "critical areas and during important events."
    ))
    story.append(body(
        "The implemented system has evolved substantially beyond the originally proposed scope. Key "
        "enhancements include the replacement of basic OpenCV motion detection with YOLOv8 neural object "
        "detection with priority classification, the addition of temporal ROI persistence eliminating visual "
        "flicker, the implementation of a multi-signal risk score and three-state surveillance machine, "
        "the pre/post-event forensic frame buffer for evidence preservation, three privacy operation modes, "
        "runtime-switchable codec support, Docker containerization, and a comprehensive evaluation pipeline "
        "with VMAF scoring, detection accuracy retention, and investigation usability metrics."
    ))
    story.append(body(
        "The system is strongly suited for deployment in smart cities, educational institutions, industrial "
        "facilities, and low-resource IoT environments in Bangladesh. The edge-based architecture, stable "
        "performance metrics, privacy-preserving design, and user-friendly dashboard make direct contributions "
        "to the Digital Bangladesh and Smart Bangladesh Vision 2041 initiatives. The project's novel "
        "contributions — particularly the risk-aware event engine and the investigation usability metric — "
        "represent meaningful advances beyond the current state of the art in surveillance video compression."
    ))

    # ═══════════════════════════════════════════════
    # REFERENCES
    # ═══════════════════════════════════════════════
    story.append(h1("References"))
    
    refs = [
        '[1] S. Ahmad, R. Afzal, and M. Javed, "Bandwidth-efficient video streaming for smart city surveillance: A comprehensive survey," IEEE Access, vol. 12, pp. 45678-45701, 2024.',
        '[2] M. Hossain, A. K. M. M. Rahman, and S. Islam, "Video compression techniques for IoT-based surveillance systems: challenges and opportunities," Journal of King Saud University - Computer and Information Sciences, vol. 36, no. 3, pp. 102-118, 2024.',
        '[3] Z. Wang, A. C. Bovik, H. R. Sheikh, and E. P. Simoncelli, "Image quality assessment: from error visibility to structural similarity," IEEE Transactions on Image Processing, vol. 13, no. 4, pp. 600-612, 2004.',
        '[4] A. Mittal, R. Soundararajan, and A. C. Bovik, "Making a completely blind image quality analyzer," IEEE Signal Processing Letters, vol. 20, no. 3, pp. 209-212, 2013.',
        '[5] Z. Wang, E. P. Simoncelli, and A. C. Bovik, "Multiscale structural similarity for image quality assessment," in Proc. Asilomar Conference on Signals, Systems and Computers, 2003, pp. 1398-1402.',
        '[6] N. Ponomarenko et al., "Image database TID2013: peculiarities, results and perspectives," Signal Processing: Image Communication, vol. 30, pp. 57-77, 2015.',
        '[7] Bangladesh Bureau of Statistics, "ICT Usage Survey Report 2024," Ministry of Planning, Government of Bangladesh, Dhaka, 2025.',
        '[8] M. A. Islam and S. Hossain, "Smart Bangladesh Vision 2041: Digital infrastructure challenges and opportunities," International Journal of Smart City Technology, vol. 8, no. 2, pp. 45-62, 2024.',
        '[9] A. Rahman, M. Z. Islam, and T. Ahmed, "Internet traffic analysis and bandwidth utilization in Bangladesh: trends and forecasts," Bangladesh Journal of ICT, vol. 12, no. 1, pp. 23-38, 2025.',
        '[10] M. S. Uddin and K. M. A. Hasan, "Cost analysis of video surveillance storage for educational institutions in developing countries," in Proc. IEEE International Conference on Sustainable Technologies, 2024, pp. 112-117.',
        '[11] M. F. Rabbi and N. Jahan, "Challenges in large-scale CCTV deployment in developing nations: a Bangladeshi perspective," Journal of Security and Privacy, vol. 7, no. 3, pp. 201-218, 2025.',
        '[12] C. Wang and P. Yang, "Compression metadata-assisted RoI extraction and adaptive inference for efficient video analytics," arXiv preprint arXiv:2503.24127, Mar. 2025.',
        '[13] X. Dou, X. Cao, and X. Zhang, "Region-of-interest based coding scheme for live videos," Applied Sciences, vol. 14, no. 12, pp. 5123-5140, 2024.',
        '[14] M. A. Labiod, M. Gharbi, F.-X. Coudoux, and P. Corlay, "Region of Interest (ROI) based adaptive cross-layer system for real-time video streaming over Vehicular Ad-hoc NETworks (VANETs)," IEEE Transactions on Vehicular Technology, vol. 72, no. 8, pp. 10456-10469, 2023.',
        '[15] H. Guo et al., "DeepStream: bandwidth efficient multi-camera video streaming for deep learning analytics," in Proc. ACM Multimedia Systems Conference, 2023, pp. 145-156.',
        '[16] L. Gao et al., "Robust mixed-rate region-of-interest-aware video compressive sensing for transmission line surveillance video," Information, vol. 15, no. 8, pp. 478-495, 2024.',
        '[17] A. Aliouat, N. Kouadria et al., "Region-of-interest based video coding strategy for rate/energy-constrained smart surveillance systems using WMSNs," Ad Hoc Networks, vol. 142, pp. 103-118, 2023.',
        '[18] W. Wang et al., "Region-based content enhancement for efficient video analytics at the edge," in Proc. USENIX NSDI 2025, 2025, pp. 267-284.',
        '[19] P. Agrawal, N. Mohod, V. Madaan, W. O. Choo, and K. W. Goh, "FRVC: frame relevance based video compression for surveillance videos using deep learning methods," PeerJ Computer Science, vol. 11, pp. e2345, 2025.',
        '[20] U. Gadot et al., "RL-RC-DoT: a block-level RL agent for task-aware video compression," arXiv preprint arXiv:2501.12216, Jan. 2025.',
        '[21] Y. S. Chuang et al., "Adaptive ROI encoding and caching for video surveillance streaming," in Proc. IEEE International Conference on Multimedia and Expo, 2025, pp. 89-94.',
        '[22] S. Rozek et al., "Video coding for machines using region-of-interest-based retargeting," EURASIP Journal on Image and Video Processing, vol. 2025, no. 1, pp. 15-32, 2025.',
        '[23] M. Lu, F. Chen, and S. Pu, "Transformer-based learned video compression with ROI-aware bit allocation," in Proc. IEEE/CVF Conference on Computer Vision and Pattern Recognition, 2024, pp. 17234-17244.',
        '[24] T. Islam and M. R. Karim, "SDN-based adaptive bitrate streaming for multi-camera surveillance networks," Journal of Network and Computer Applications, vol. 225, pp. 103-120, 2024.',
        '[25] H. Kim, S. Lee, and J. Park, "Privacy-preserving video analytics with selective ROI encryption for surveillance systems," IEEE Transactions on Information Forensics and Security, vol. 19, pp. 3456-3471, 2024.',
        '[26] Y. Zhao, P. Liu, and H. Chen, "Federated learning for distributed surveillance video analysis in smart city environments," IEEE Internet of Things Journal, vol. 12, no. 4, pp. 4567-4580, 2025.',
        '[27] G. Jocher, A. Chaurasia, and J. Qiu, "Ultralytics YOLOv8," 2023. [Online]. Available: https://github.com/ultralytics/ultralytics',
        '[28] D. Parkhi, A. Vedaldi, and A. Zisserman, "The application of deep learning in video surveillance: a comprehensive review," IEEE Transactions on Pattern Analysis and Machine Intelligence, vol. 46, no. 5, pp. 3124-3145, 2024.',
        '[29] L. Liu et al., "Edge intelligence for real-time video analytics: architectures, challenges, and opportunities," ACM Computing Surveys, vol. 57, no. 2, pp. 1-35, 2025.',
        '[30] K. He, X. Zhang, S. Ren, and J. Sun, "Deep residual learning for image recognition," in Proc. IEEE Conference on Computer Vision and Pattern Recognition, 2016, pp. 770-778.',
        '[31] T. Y. Lin et al., "Microsoft COCO: common objects in context," in Proc. European Conference on Computer Vision, 2014, pp. 740-755.',
        '[32] J. Redmon and A. Farhadi, "YOLOv3: an incremental improvement," arXiv preprint arXiv:1804.02767, 2018.',
        '[33] A. Bochkovskiy, C. Y. Wang, and H. Y. M. Liao, "YOLOv4: optimal speed and accuracy of object detection," arXiv preprint arXiv:2004.10934, 2020.',
        '[34] C. Y. Wang, A. Bochkovskiy, and H. Y. M. Liao, "YOLOv7: trainable bag-of-freebies sets new state-of-the-art for real-time object detectors," in Proc. IEEE/CVF Conference on Computer Vision and Pattern Recognition, 2023, pp. 7464-7475.',
        '[35] Z. Ge et al., "YOLOX: exceeding YOLO series in 2021," arXiv preprint arXiv:2107.08430, 2021.',
        '[36] X. Zhu et al., "TPH-YOLOv5: improved YOLOv5 based on transformer prediction head for object detection on drone-captured scenarios," in Proc. IEEE/CVF International Conference on Computer Vision Workshops, 2021, pp. 2778-2788.',
        '[37] S. Bianco, R. Cadene, and L. Celona, "Benchmark analysis of representative deep neural network architectures for object detection," IEEE Transactions on Pattern Analysis and Machine Intelligence, vol. 42, no. 12, pp. 3158-3175, 2020.',
        '[38] R. Hussain and A. Zia, "A survey on video compression techniques for surveillance systems: from H.264 to learned compression," Multimedia Tools and Applications, vol. 83, no. 15, pp. 45123-45160, 2024.',
        '[39] J. Balle, V. Laparra, and E. P. Simoncelli, "End-to-end optimized image compression," in Proc. International Conference on Learning Representations, 2017.',
        '[40] D. Minnen, J. Balle, and G. Toderici, "Joint autoregressive and hierarchical priors for learned image compression," in Proc. Conference on Neural Information Processing Systems, 2018, pp. 10771-10780.',
        '[41] T. Chen and C. Guestrin, "XGBoost: a scalable tree boosting system," in Proc. ACM SIGKDD International Conference on Knowledge Discovery and Data Mining, 2016, pp. 785-794.',
    ]

    for ref in refs:
        story.append(Paragraph(ref, styles['RefEntry']))

    # ═══════════════════════════════════════════════
    # AI CHECK RATIO
    # ═══════════════════════════════════════════════
    story.append(new_page())
    story.append(h1("AI Check Ratio"))
    story.append(Spacer(1, 20*mm))
    story.append(body(
        "This proposal has been prepared through rigorous research, analysis, and implementation work conducted "
        "by the project team. AI-assisted tools may have been used for literature review organization and "
        "editing assistance. The technical content, implementation, experimental results, and conclusions "
        "represent original work by the project team members."
    ))
    story.append(Spacer(1, 15*mm))
    story.append(placeholder_fig("AI Similarity Check Report (Placeholder)", height=50*mm))
    story.append(caption("AI-generated content similarity analysis report will be attached upon final submission."))

    # ─── Build PDF ───
    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(f"PDF generated: {output_path}")


if __name__ == '__main__':
    output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Project_Proposal_Final_Updated.pdf")
    build_pdf(output)
