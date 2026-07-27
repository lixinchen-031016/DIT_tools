"""报告生成服务"""
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from DITWorkstation.App import config
from DITWorkstation.Models import (
    Project, BackupJob, MediaAsset, ShootingLog, RATING_LABELS, AssetRating
)
from DITWorkstation.Utils import format_size, logger


class ReportService:
    """报告生成服务"""

    def __init__(self):
        self._font_registered = False
        self._chinese_font_name = "ChineseFont"

    def _register_fonts(self):
        """注册中文字体（跨平台兼容）"""
        if self._font_registered:
            return

        font_paths = self._get_system_font_paths()

        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    pdfmetrics.registerFont(TTFont(self._chinese_font_name, fp, subfontIndex=0))
                    self._font_registered = True
                    logger.info(f"成功注册中文字体: {fp}")
                    return
                except Exception as e:
                    logger.warning(f"注册字体失败 {fp}: {e}")
                    continue

        logger.warning("未找到中文字体，使用默认字体")

    def _get_system_font_paths(self) -> List[str]:
        """获取系统字体路径（跨平台）"""
        system = platform.system()
        font_paths = []

        if system == "Darwin":
            font_paths.extend([
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/System/Library/Fonts/Hiragino Sans GB.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
            ])
        elif system == "Windows":
            font_paths.extend([
                "C:/Windows/Fonts/msyh.ttc",
                "C:/Windows/Fonts/simsun.ttc",
                "C:/Windows/Fonts/arialuni.ttf",
            ])
        elif system == "Linux":
            font_paths.extend([
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ])
        else:
            font_paths.append("Helvetica")

        return font_paths

    def generate_backup_report(
        self,
        project: Optional[Project],
        jobs: List[BackupJob],
        output_path: Optional[str] = None
    ) -> str:
        """
        生成数据备份报告

        Args:
            project: 项目信息
            jobs: 备份作业列表
            output_path: 输出路径

        Returns:
            报告文件路径
        """
        self._register_fonts()

        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(config.report_dir / f"备份报告_{timestamp}.pdf")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(output_path, pagesize=A4,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ChineseTitle', parent=styles['Title'],
            fontName=self._chinese_font_name, fontSize=18
        )
        heading_style = ParagraphStyle(
            'ChineseHeading', parent=styles['Heading2'],
            fontName=self._chinese_font_name, fontSize=14
        )
        normal_style = ParagraphStyle(
            'ChineseNormal', parent=styles['Normal'],
            fontName=self._chinese_font_name, fontSize=10
        )

        elements = []

        elements.append(Paragraph("DIT数据管理报告", title_style))
        elements.append(Spacer(1, 10*mm))

        elements.append(Paragraph("项目信息", heading_style))
        project_info = [
            ["项目名称", project.name if project else "未指定"],
            ["报告时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["备份任务数", str(len(jobs))],
        ]
        t = Table(project_info, colWidths=[40*mm, 120*mm])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), self._chinese_font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 8*mm))

        elements.append(Paragraph("备份任务详情", heading_style))
        for i, job in enumerate(jobs):
            job_data = [
                ["任务ID", job.job_id],
                ["源路径", job.source_path],
                ["文件数量", str(job.total_files)],
                ["总大小", self._format_size(job.total_bytes)],
                ["状态", job.status.value],
                ["校验算法", job.algorithm.value],
                ["创建时间", job.created_at.strftime("%Y-%m-%d %H:%M:%S")],
            ]
            t = Table(job_data, colWidths=[40*mm, 120*mm])
            t.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), self._chinese_font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
            ]))
            elements.append(t)

            if job.targets:
                target_header = [["目标", "状态", "文件数", "已验证"]]
                target_rows = []
                for target in job.targets:
                    target_rows.append([
                        target.name or target.path,
                        target.status.value,
                        f"{target.completed_files}/{target.total_files}",
                        "是" if target.verified else "否"
                    ])
                t2 = Table(target_header + target_rows, colWidths=[50*mm, 35*mm, 35*mm, 30*mm])
                t2.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, -1), self._chinese_font_name),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ]))
                elements.append(Spacer(1, 3*mm))
                elements.append(t2)

            elements.append(Spacer(1, 6*mm))

        doc.build(elements)
        logger.info(f"备份报告生成成功: {output_path}")
        return output_path

    def generate_asset_report(
        self,
        project: Project,
        assets: List[MediaAsset],
        logs: List[ShootingLog],
        output_path: Optional[str] = None
    ) -> str:
        """
        生成素材统计报告

        Args:
            project: 项目
            assets: 素材列表
            logs: 拍摄日志
            output_path: 输出路径

        Returns:
            报告文件路径
        """
        self._register_fonts()

        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(config.report_dir / f"素材报告_{timestamp}.pdf")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(output_path, pagesize=A4,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ChineseTitle', parent=styles['Title'],
            fontName=self._chinese_font_name, fontSize=18
        )
        heading_style = ParagraphStyle(
            'ChineseHeading', parent=styles['Heading2'],
            fontName=self._chinese_font_name, fontSize=14
        )

        elements = []
        elements.append(Paragraph("素材统计报告", title_style))
        elements.append(Spacer(1, 10*mm))

        elements.append(Paragraph("统计概览", heading_style))
        total_size = sum(a.file_size for a in assets)
        raw_count = sum(1 for a in assets if a.file_type in config.raw_extensions)
        jpg_count = sum(1 for a in assets if a.file_type in ('.jpg', '.jpeg'))

        # 评级分布：仅统计已评级（rating > 0）的素材
        rated_count = sum(1 for a in assets if a.rating and a.rating > 0)
        rating_dist = {r: 0 for r in RATING_LABELS}
        for a in assets:
            r = a.rating or 0
            rating_dist[r] = rating_dist.get(r, 0) + 1

        stats = [
            ["项目", project.name],
            ["素材总数", str(len(assets))],
            ["RAW文件数", str(raw_count)],
            ["JPG文件数", str(jpg_count)],
            ["总数据量", self._format_size(total_size)],
            ["拍摄日志数", str(len(logs))],
            ["已评级素材", f"{rated_count} / {len(assets)}"],
            ["报告时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ]
        t = Table(stats, colWidths=[40*mm, 120*mm])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), self._chinese_font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 8*mm))

        elements.append(Paragraph("按场景统计", heading_style))
        scene_stats: Dict[str, int] = {}
        for a in assets:
            scene = a.scene or "未分类"
            scene_stats[scene] = scene_stats.get(scene, 0) + 1

        if scene_stats:
            scene_data = [["场景", "文件数"]]
            for scene, count in sorted(scene_stats.items()):
                scene_data.append([scene, str(count)])
            t = Table(scene_data, colWidths=[80*mm, 40*mm])
            t.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), self._chinese_font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ]))
            elements.append(t)
            elements.append(Spacer(1, 8*mm))

        elements.append(Paragraph("按评级统计", heading_style))
        rating_data = [["评级", "文件数", "占比"]]
        total = len(assets) or 1  # 避免除零
        for r in sorted(RATING_LABELS.keys()):
            count = rating_dist.get(r, 0)
            percent = f"{(count / total) * 100:.1f}%"
            rating_data.append([RATING_LABELS[r], str(count), percent])
        t = Table(rating_data, colWidths=[60*mm, 40*mm, 40*mm])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), self._chinese_font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            # 优选行高亮：表头占 index 0，rating=PREFERRED(3) 对应行索引 = 3 + 1 = 4
            ('BACKGROUND', (0, AssetRating.PREFERRED.value + 1),
             (-1, AssetRating.PREFERRED.value + 1), colors.HexColor('#FFF4E5')),
        ]))
        elements.append(t)

        doc.build(elements)
        logger.info(f"素材报告生成成功: {output_path}")
        return output_path

    def _format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        return format_size(size_bytes)
