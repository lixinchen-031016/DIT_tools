"""报告生成服务"""

import os
import platform
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from DITWorkstation.App import config
from DITWorkstation.Models import (
    RATING_LABELS,
    AssetRating,
    BackupJob,
    BackupStatus,
    BackupTarget,
    ChecksumAlgorithm,
    CopyStatus,
    MediaAsset,
    Project,
    ShootingLog,
)
from DITWorkstation.Utils import format_size, logger, now_local


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
                    pdfmetrics.registerFont(
                        TTFont(self._chinese_font_name, fp, subfontIndex=0)
                    )
                    self._font_registered = True
                    logger.info(f"成功注册中文字体: {fp}")
                    return
                except Exception as e:
                    logger.warning(f"注册字体失败 {fp}: {e}")
                    continue

        # ReportLab 内置 CID 字体不依赖系统字体文件，在精简 Windows/macOS
        # 设备上也能生成中文 PDF。它不嵌入字体，但不会导致报告任务失败。
        self._chinese_font_name = "STSong-Light"
        pdfmetrics.registerFont(UnicodeCIDFont(self._chinese_font_name))
        self._font_registered = True
        logger.warning("未找到系统中文字体，使用 ReportLab CID 字体 STSong-Light")

    def _get_system_font_paths(self) -> list[str]:
        """获取系统字体路径（跨平台）"""
        system = platform.system()
        font_paths = []

        if system == "Darwin":
            font_paths.extend(
                [
                    "/System/Library/Fonts/PingFang.ttc",
                    "/System/Library/Fonts/STHeiti Light.ttc",
                    "/System/Library/Fonts/Hiragino Sans GB.ttc",
                    "/Library/Fonts/Arial Unicode.ttf",
                    "/System/Library/Fonts/Helvetica.ttc",
                ]
            )
        elif system == "Windows":
            font_paths.extend(
                [
                    "C:/Windows/Fonts/msyh.ttc",
                    "C:/Windows/Fonts/simsun.ttc",
                    "C:/Windows/Fonts/arialuni.ttf",
                ]
            )
        elif system == "Linux":
            font_paths.extend(
                [
                    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                ]
            )
        else:
            font_paths.append("Helvetica")

        return font_paths

    @staticmethod
    def _coerce_backup_job(record) -> BackupJob:
        """将数据库备份历史或领域对象统一为 ``BackupJob``。"""
        if isinstance(record, BackupJob):
            return record
        targets = []
        for raw in record.get("targets", []):
            try:
                status = CopyStatus(raw.get("status", CopyStatus.PENDING.value))
            except ValueError:
                status = CopyStatus.FAILED
            targets.append(
                BackupTarget(
                    path=raw.get("path", ""),
                    name=raw.get("name", ""),
                    status=status,
                    total_files=int(raw.get("total_files", 0)),
                    completed_files=int(raw.get("completed_files", 0)),
                    total_bytes=int(raw.get("total_bytes", 0)),
                    copied_bytes=int(raw.get("copied_bytes", 0)),
                    verified=bool(raw.get("verified", False)),
                    error_message=raw.get("error_message", ""),
                    failed_files=list(raw.get("failed_files", [])),
                    pending_files=list(raw.get("pending_files", [])),
                )
            )
        try:
            status = BackupStatus(record.get("status", BackupStatus.IDLE.value))
        except ValueError:
            status = BackupStatus.FAILED
        try:
            algorithm = ChecksumAlgorithm(
                record.get("algorithm", ChecksumAlgorithm.XXHASH64.value)
            )
        except ValueError:
            algorithm = ChecksumAlgorithm.XXHASH64
        created_at = (
            ReportService._safe_datetime(record.get("created_at")) or now_local()
        )
        completed_at = ReportService._safe_datetime(record.get("completed_at"))
        return BackupJob(
            job_id=record.get("job_id", "unknown"),
            source_path=record.get("source_path", ""),
            targets=targets,
            status=status,
            algorithm=algorithm,
            total_files=int(record.get("total_files", 0)),
            total_bytes=int(record.get("total_bytes", 0)),
            created_at=created_at,
            completed_at=completed_at,
        )

    @staticmethod
    def _safe_datetime(value) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def generate_backup_report(
        self,
        project: Project | None,
        jobs: list[BackupJob | dict],
        output_path: str | None = None,
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
        jobs = [self._coerce_backup_job(job) for job in jobs]

        if not output_path:
            timestamp = now_local().strftime("%Y%m%d_%H%M%S")
            output_path = str(config.report_dir / f"备份报告_{timestamp}.pdf")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            output_path, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ChineseTitle",
            parent=styles["Title"],
            fontName=self._chinese_font_name,
            fontSize=18,
        )
        heading_style = ParagraphStyle(
            "ChineseHeading",
            parent=styles["Heading2"],
            fontName=self._chinese_font_name,
            fontSize=14,
        )
        elements = []

        elements.append(Paragraph("DIT数据管理报告", title_style))
        elements.append(Spacer(1, 10 * mm))

        elements.append(Paragraph("项目信息", heading_style))
        project_info = [
            ["项目名称", project.name if project else "未指定"],
            ["报告时间", now_local().strftime("%Y-%m-%d %H:%M:%S")],
            ["备份任务数", str(len(jobs))],
        ]
        t = Table(project_info, colWidths=[40 * mm, 120 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), self._chinese_font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ]
            )
        )
        elements.append(t)
        elements.append(Spacer(1, 8 * mm))

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
            t = Table(job_data, colWidths=[40 * mm, 120 * mm])
            t.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), self._chinese_font_name),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                    ]
                )
            )
            elements.append(t)

            if job.targets:
                target_header = [["目标", "状态", "文件数", "已验证"]]
                target_rows = []
                for target in job.targets:
                    target_rows.append(
                        [
                            target.name or target.path,
                            target.status.value,
                            f"{target.completed_files}/{target.total_files}",
                            "是" if target.verified else "否",
                        ]
                    )
                t2 = Table(
                    target_header + target_rows,
                    colWidths=[50 * mm, 35 * mm, 35 * mm, 30 * mm],
                )
                t2.setStyle(
                    TableStyle(
                        [
                            ("FONTNAME", (0, 0), (-1, -1), self._chinese_font_name),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ]
                    )
                )
                elements.append(Spacer(1, 3 * mm))
                elements.append(t2)

            elements.append(Spacer(1, 6 * mm))

        doc.build(elements)
        logger.info(f"备份报告生成成功: {output_path}")
        return output_path

    def generate_audit_report(
        self,
        operations: Iterable[dict],
        output_path: str | None = None,
        *,
        title: str = "操作审计报表",
    ) -> str:
        """将筛选后的操作日志生成带汇总的 PDF 交付报表。"""
        self._register_fonts()
        records = list(operations or [])
        if not output_path:
            timestamp = now_local().strftime("%Y%m%d_%H%M%S")
            output_path = str(config.report_dir / f"审计报表_{timestamp}.pdf")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            output_path, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "AuditTitle",
            parent=styles["Title"],
            fontName=self._chinese_font_name,
            fontSize=18,
        )
        heading_style = ParagraphStyle(
            "AuditHeading",
            parent=styles["Heading2"],
            fontName=self._chinese_font_name,
            fontSize=13,
        )
        body_style = ParagraphStyle(
            "AuditBody",
            parent=styles["BodyText"],
            fontName=self._chinese_font_name,
            fontSize=8,
        )

        status_counts = Counter(
            str(item.get("status") or "success") for item in records
        )
        event_counts = Counter(str(item.get("event") or "未命名") for item in records)
        day_counts = Counter(
            self._safe_datetime(item.get("created_at")).strftime("%Y-%m-%d")
            for item in records
            if self._safe_datetime(item.get("created_at"))
        )
        status_labels = {
            "success": "成功",
            "not_found": "未找到",
            "conflict": "冲突",
            "invalid": "无效",
            "error": "失败",
            "cancelled": "已取消",
        }
        elements = [Paragraph(title, title_style), Spacer(1, 6 * mm)]
        summary = [
            ["记录总数", str(len(records))],
            ["成功", str(status_counts.get("success", 0))],
            [
                "异常/失败",
                str(
                    sum(
                        count
                        for key, count in status_counts.items()
                        if key != "success"
                    )
                ),
            ],
            ["报表时间", now_local().strftime("%Y-%m-%d %H:%M:%S")],
        ]
        table = Table(summary, colWidths=[42 * mm, 118 * mm])
        table.setStyle(self._report_table_style())
        elements.extend([Paragraph("汇总", heading_style), table, Spacer(1, 5 * mm)])

        daily_rows = [["日期", "操作次数"]]
        daily_rows.extend([list(row) for row in sorted(day_counts.items())])
        event_rows = [["事件", "次数"]]
        event_rows.extend([list(row) for row in event_counts.most_common()])
        for heading, rows in (("按日统计", daily_rows), ("按事件统计", event_rows)):
            table = Table(rows or [["无", "0"]], colWidths=[90 * mm, 35 * mm])
            table.setStyle(self._report_table_style(header=True))
            elements.extend(
                [Paragraph(heading, heading_style), table, Spacer(1, 5 * mm)]
            )

        detail_rows = [["时间", "事件", "状态", "对象", "详情"]]
        for item in records:
            created = self._safe_datetime(item.get("created_at"))
            detail_rows.append(
                [
                    created.strftime("%Y-%m-%d %H:%M:%S") if created else "",
                    Paragraph(str(item.get("event") or ""), body_style),
                    status_labels.get(
                        str(item.get("status") or "success"),
                        str(item.get("status") or ""),
                    ),
                    f"{item.get('object_type') or ''}/{item.get('object_id') or ''}".strip(
                        "/"
                    ),
                    Paragraph(str(item.get("detail") or ""), body_style),
                ]
            )
        detail = Table(
            detail_rows or [["", "", "", "", ""]],
            colWidths=[29 * mm, 31 * mm, 20 * mm, 37 * mm, 43 * mm],
            repeatRows=1,
        )
        detail.setStyle(self._report_table_style(header=True))
        elements.extend([Paragraph("明细", heading_style), detail])
        doc.build(elements)
        logger.info(f"审计报表生成成功: {output_path}")
        return output_path

    def _report_table_style(self, header: bool = False) -> TableStyle:
        commands = [
            ("FONTNAME", (0, 0), (-1, -1), self._chinese_font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if header:
            commands.extend(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ]
            )
        else:
            commands.append(("BACKGROUND", (0, 0), (0, -1), colors.lightgrey))
        return TableStyle(commands)

    def generate_asset_report(
        self,
        project: Project,
        assets: Iterable[MediaAsset],
        logs: list[ShootingLog],
        output_path: str | None = None,
        *,
        total: int = 0,
        progress_callback=None,
        cancel_check=None,
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
            timestamp = now_local().strftime("%Y%m%d_%H%M%S")
            output_path = str(config.report_dir / f"素材报告_{timestamp}.pdf")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            output_path, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ChineseTitle",
            parent=styles["Title"],
            fontName=self._chinese_font_name,
            fontSize=18,
        )
        heading_style = ParagraphStyle(
            "ChineseHeading",
            parent=styles["Heading2"],
            fontName=self._chinese_font_name,
            fontSize=14,
        )

        elements = []
        elements.append(Paragraph("素材统计报告", title_style))
        elements.append(Spacer(1, 10 * mm))

        elements.append(Paragraph("统计概览", heading_style))
        total_size = 0
        raw_count = 0
        jpg_count = 0
        rated_count = 0
        asset_count = 0
        rating_dist = {r: 0 for r in RATING_LABELS}
        scene_stats: dict[str, int] = {}
        for a in assets:
            if cancel_check and cancel_check():
                raise InterruptedError("报告生成已取消")
            asset_count += 1
            total_size += a.file_size
            raw_count += int(a.file_type in config.raw_extensions)
            jpg_count += int(a.file_type in (".jpg", ".jpeg"))
            rated_count += int(bool(a.rating and a.rating > 0))
            r = a.rating or 0
            rating_dist[r] = rating_dist.get(r, 0) + 1
            scene = a.scene or "未分类"
            scene_stats[scene] = scene_stats.get(scene, 0) + 1
            if progress_callback:
                progress_callback(asset_count, total, f"统计: {a.file_name}")

        stats = [
            ["项目", project.name],
            ["素材总数", str(asset_count)],
            ["RAW文件数", str(raw_count)],
            ["JPG文件数", str(jpg_count)],
            ["总数据量", self._format_size(total_size)],
            ["拍摄日志数", str(len(logs))],
            ["已评级素材", f"{rated_count} / {asset_count}"],
            ["报告时间", now_local().strftime("%Y-%m-%d %H:%M:%S")],
        ]
        t = Table(stats, colWidths=[40 * mm, 120 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), self._chinese_font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ]
            )
        )
        elements.append(t)
        elements.append(Spacer(1, 8 * mm))

        elements.append(Paragraph("按场景统计", heading_style))
        if scene_stats:
            scene_data = [["场景", "文件数"]]
            for scene, count in sorted(scene_stats.items()):
                scene_data.append([scene, str(count)])
            t = Table(scene_data, colWidths=[80 * mm, 40 * mm])
            t.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), self._chinese_font_name),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ]
                )
            )
            elements.append(t)
            elements.append(Spacer(1, 8 * mm))

        elements.append(Paragraph("按评级统计", heading_style))
        rating_data = [["评级", "文件数", "占比"]]
        total_assets = asset_count or 1  # 避免除零
        for r in sorted(RATING_LABELS.keys()):
            count = rating_dist.get(r, 0)
            percent = f"{(count / total_assets) * 100:.1f}%"
            rating_data.append([RATING_LABELS[r], str(count), percent])
        t = Table(rating_data, colWidths=[60 * mm, 40 * mm, 40 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), self._chinese_font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    # 优选行高亮：表头占 index 0，rating=PREFERRED(3) 对应行索引 = 3 + 1 = 4
                    (
                        "BACKGROUND",
                        (0, AssetRating.PREFERRED.value + 1),
                        (-1, AssetRating.PREFERRED.value + 1),
                        colors.HexColor("#FFF4E5"),
                    ),
                ]
            )
        )
        elements.append(t)

        doc.build(elements)
        logger.info(f"素材报告生成成功: {output_path}")
        return output_path

    def _format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        return format_size(size_bytes)

    def export_assets_csv(self, assets: list[MediaAsset], output_path: str) -> str:
        """导出素材 CSV；兼容旧接口，内部统一使用可迭代数据源。"""
        return self.export_assets_csv_iter(assets, output_path)

    def export_assets_csv_iter(
        self,
        assets,
        output_path: str,
        *,
        total: int = 0,
        progress_callback=None,
        cancel_check=None,
    ) -> str:
        """把素材元数据导出为 CSV 表格。

        使用 utf-8-sig（带 BOM）编码，Excel 在 Windows/macOS 上均可直接
        打开且中文不乱码；供编辑、转码交接或外部目录系统导入使用。

        Args:
            assets: 要导出的素材列表或任意素材迭代器
            output_path: 输出 .csv 路径

        Returns:
            实际写入的路径
        """
        import csv

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        headers = [
            "文件名",
            "文件路径",
            "类型",
            "素材类型",
            "大小(字节)",
            "场景",
            "镜头",
            "镜次",
            "评级",
            "拍摄时间",
            "相机品牌",
            "相机型号",
            "镜头型号",
            "焦距",
            "分辨率",
            "时长(秒)",
            "校验和算法",
            "校验和",
            "备份位置",
            "原始路径",
            "关联日志ID",
            "是否工作副本",
            "标签",
            "备注",
            "导入时间",
        ]

        def _dt(value) -> str:
            return value.isoformat() if value else ""

        count = 0
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for a in assets:
                if cancel_check and cancel_check():
                    raise InterruptedError("CSV 导出已取消")
                writer.writerow(
                    [
                        a.file_name,
                        a.file_path,
                        a.file_type,
                        a.asset_type,
                        a.file_size,
                        a.scene,
                        a.shot,
                        a.take,
                        RATING_LABELS.get(
                            a.rating, RATING_LABELS[AssetRating.NONE.value]
                        ),
                        _dt(a.date_taken),
                        a.camera_make,
                        a.camera_model,
                        a.lens_model,
                        a.focal_length,
                        f"{a.width}x{a.height}" if a.width and a.height else "",
                        f"{a.duration_seconds:.3f}".rstrip("0").rstrip(".")
                        if a.duration_seconds
                        else "",
                        a.checksum_algorithm,
                        a.checksum_value,
                        " | ".join(a.backup_locations),
                        a.original_path,
                        a.log_id or "",
                        "是" if a.is_working_copy else "否",
                        a.tags,
                        a.notes,
                        _dt(a.date_imported),
                    ]
                )
                count += 1
                if progress_callback:
                    progress_callback(count, total, f"导出: {a.file_name}")

        logger.info(f"素材 CSV 导出成功: {path}（{count} 条）")
        return str(path)
