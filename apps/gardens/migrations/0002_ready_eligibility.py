from django.db import migrations, models
from django.db.models import OuterRef, Subquery

READY_MOISTURE_LIMIT = 40


def demote_ineligible_ready_troughs(apps, schema_editor):
    """把状态为「可下槽」但不满足资格的存量槽位退回「萎凋中」。

    资格口径与新模型 Trough.ready_block_reason() 一致：
    无最新批次、最新批次实测为空或实测 > 40%。
    迁移使用历史模型，显式写出子查询，不依赖新代码。
    """
    Trough = apps.get_model("gardens", "Trough")
    WitherBatch = apps.get_model("gardens", "WitherBatch")

    latest_actual = Subquery(
        WitherBatch.objects.filter(trough=OuterRef("pk"))
        .order_by("-startedAt", "-id")
        .values("actualMoisture")[:1]
    )
    eligible_ids = set(
        Trough.objects.annotate(latest=latest_actual)
        .exclude(latest__isnull=True)
        .filter(latest__lte=READY_MOISTURE_LIMIT)
        .values_list("id", flat=True)
    )
    Trough.objects.filter(status="ready").exclude(id__in=eligible_ids).update(
        status="withering"
    )


def noop_reverse(apps, schema_editor):
    # 反向迁移不恢复（旧的失格「可下槽」状态本就是脏数据）。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("gardens", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            demote_ineligible_ready_troughs,
            noop_reverse,
        ),
    ]
